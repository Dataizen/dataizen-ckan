# Filtrage lors du moissonnage CSW dans CKAN

## Vue d'ensemble

Le moissonnage CSW (Catalogue Service for the Web) dans CKAN permet de filtrer les métadonnées à plusieurs niveaux :
1. **Au niveau du harvester CSW** : Filtrage lors de la récupération des métadonnées depuis le serveur CSW
2. **Au niveau de CKAN** : Filtrage après l'import des métadonnées

---

## Options de filtrage disponibles

### 1. Filtrage via GetRecords (OGC CSW)

Le harvester CSW utilise les paramètres standard OGC CSW `GetRecords` pour filtrer à la source.

#### Configuration dans l'interface CKAN

Lors de la création/modification d'une source de moissonnage CSW, vous pouvez configurer des filtres dans le champ **"Configuration"** (JSON) :

```json
{
  "default_tags": ["harvested", "csw"],
  "default_groups": ["geodata"],
  "default_extras": {
    "harvest_catalogue_name": "Catalogue CSW"
  },
  "override_extras": {
    "source": "CSW"
  },
  "validator_profiles": ["iso19139"],
  "cql": "dc:type = 'dataset'",
  "constraints": {
    "type": "Filter",
    "constraint": [
      {
        "type": "PropertyIsEqualTo",
        "propertyname": "dc:type",
        "literal": "dataset"
      }
    ]
  }
}
```

#### Paramètres de filtrage disponibles

##### a) Filtrage par type de ressource (`dc:type`)

```json
{
  "cql": "dc:type = 'dataset'"
}
```

Ou avec OGC Filter :

```json
{
  "constraints": {
    "type": "Filter",
    "constraint": {
      "type": "PropertyIsEqualTo",
      "propertyname": "dc:type",
      "literal": "dataset"
    }
  }
}
```

##### b) Filtrage par étendue géographique

```json
{
  "constraints": {
    "type": "Filter",
    "constraint": {
      "type": "BBOX",
      "propertyname": "ows:BoundingBox",
      "lowercorner": "2.0 46.0",
      "uppercorner": "7.5 48.5"
    }
  }
}
```

##### c) Filtrage par date

```json
{
  "constraints": {
    "type": "Filter",
    "constraint": {
      "type": "PropertyIsGreaterThan",
      "propertyname": "dc:date",
      "literal": "2020-01-01"
    }
  }
}
```

##### d) Filtrage par organisation/producteur

```json
{
  "cql": "dc:publisher = 'DREAL Bourgogne-Franche-Comté'"
}
```

##### e) Filtrage par mots-clés

```json
{
  "cql": "dc:subject LIKE '%environnement%'"
}
```

##### f) Filtrage combiné (AND/OR)

```json
{
  "constraints": {
    "type": "Filter",
    "constraint": {
      "type": "And",
      "constraint": [
        {
          "type": "PropertyIsEqualTo",
          "propertyname": "dc:type",
          "literal": "dataset"
        },
        {
          "type": "BBOX",
          "propertyname": "ows:BoundingBox",
          "lowercorner": "2.0 46.0",
          "uppercorner": "7.5 48.5"
        }
      ]
    }
  }
}
```

---

## Configuration pratique

### Via l'interface web CKAN

1. **Accéder à la gestion des sources de moissonnage** :
   - Menu Admin → Sources de moissonnage
   - Ou directement : `/harvest`

2. **Créer/Modifier une source CSW** :
   - Cliquer sur "Nouvelle source" ou modifier une source existante
   - Type : **CSW**
   - URL : URL du serveur CSW (ex: `https://catalogue.example.com/csw`)
   - **Configuration** : JSON avec les filtres (voir exemples ci-dessus)

### Via l'API CKAN

#### Créer une source CSW avec filtres

```bash
curl -X POST "http://localhost:5000/api/3/action/harvest_source_create" \
  -H "Authorization: YOUR_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "url": "https://catalogue.example.com/csw",
    "type": "csw",
    "title": "Catalogue CSW avec filtres",
    "config": {
      "default_tags": ["harvested", "csw"],
      "cql": "dc:type = '\''dataset'\'' AND dc:subject LIKE '\''%environnement%'\''",
      "constraints": {
        "type": "Filter",
        "constraint": {
          "type": "BBOX",
          "propertyname": "ows:BoundingBox",
          "lowercorner": "2.0 46.0",
          "uppercorner": "7.5 48.5"
        }
      }
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
      "cql": "dc:type = '\''dataset'\'' AND dc:date > '\''2020-01-01'\''"
    }
  }'
```

---

## Exemples de configurations complètes

### Exemple 1 : Filtrage par région (Bourgogne-Franche-Comté)

```json
{
  "default_tags": ["harvested", "csw", "bfc"],
  "default_groups": ["geodata"],
  "cql": "dc:type = 'dataset'",
  "constraints": {
    "type": "Filter",
    "constraint": {
      "type": "BBOX",
      "propertyname": "ows:BoundingBox",
      "lowercorner": "2.0 46.0",
      "uppercorner": "7.5 48.5"
    }
  },
  "validator_profiles": ["iso19139"]
}
```

### Exemple 2 : Filtrage par type et date

```json
{
  "default_tags": ["harvested", "csw"],
  "cql": "dc:type = 'dataset' AND dc:date > '2020-01-01'",
  "validator_profiles": ["iso19139"]
}
```

### Exemple 3 : Filtrage par organisation et mots-clés

```json
{
  "default_tags": ["harvested", "csw"],
  "cql": "dc:publisher = 'DREAL BFC' AND (dc:subject LIKE '%eau%' OR dc:subject LIKE '%environnement%')",
  "validator_profiles": ["iso19139"]
}
```

### Exemple 4 : Filtrage combiné complexe

```json
{
  "default_tags": ["harvested", "csw"],
  "constraints": {
    "type": "Filter",
    "constraint": {
      "type": "And",
      "constraint": [
        {
          "type": "PropertyIsEqualTo",
          "propertyname": "dc:type",
          "literal": "dataset"
        },
        {
          "type": "BBOX",
          "propertyname": "ows:BoundingBox",
          "lowercorner": "2.0 46.0",
          "uppercorner": "7.5 48.5"
        },
        {
          "type": "PropertyIsGreaterThan",
          "propertyname": "dc:date",
          "literal": "2020-01-01"
        }
      ]
    }
  },
  "validator_profiles": ["iso19139"]
}
```

---

## Filtrage post-import (dans CKAN)

Après l'import, vous pouvez également filtrer les datasets moissonnés via l'API CKAN :

### Rechercher les datasets moissonnés d'une source spécifique

```bash
curl "http://localhost:5000/api/3/action/package_search?fq=extras_harvest_source_id:source-id&rows=1000" \
  -H "Authorization: YOUR_API_KEY"
```

### Filtrer par organisation

```bash
curl "http://localhost:5000/api/3/action/package_search?fq=organization:org-name&rows=1000" \
  -H "Authorization: YOUR_API_KEY"
```

### Filtrer par tags

```bash
curl "http://localhost:5000/api/3/action/package_search?fq=tags:environnement&rows=1000" \
  -H "Authorization: YOUR_API_KEY"
```

---

## Cas d'usage courants

### 1. Moissonner uniquement les datasets récents

```json
{
  "cql": "dc:date > '2023-01-01'"
}
```

### 2. Moissonner uniquement les données géospatiales d'une région

```json
{
  "constraints": {
    "type": "Filter",
    "constraint": {
      "type": "BBOX",
      "propertyname": "ows:BoundingBox",
      "lowercorner": "2.0 46.0",
      "uppercorner": "7.5 48.5"
    }
  }
}
```

### 3. Moissonner uniquement certains types de données

```json
{
  "cql": "dc:subject LIKE '%eau%' OR dc:subject LIKE '%transport%'"
}
```

### 4. Exclure certains types

```json
{
  "cql": "dc:type = 'dataset' AND NOT (dc:subject LIKE '%test%' OR dc:subject LIKE '%exemple%')"
}
```

---

## Références

- **Documentation CKAN Harvest** : https://docs.ckan.org/projects/ckanext-harvest/
- **Documentation ckanext-spatial** : https://github.com/ckan/ckanext-spatial
- **Standard OGC CSW** : https://www.ogc.org/standards/cat
- **OGC Filter Encoding** : https://www.ogc.org/standards/filter

---

## Notes importantes

1. **Syntaxe CQL vs OGC Filter** : Certains serveurs CSW supportent CQL (Common Query Language), d'autres OGC Filter. Vérifiez la documentation de votre serveur CSW.

2. **Performance** : Les filtres complexes peuvent ralentir le moissonnage. Testez d'abord avec des filtres simples.

3. **Validation** : Les métadonnées filtrées doivent toujours passer la validation ISO19139 (configurée via `validator_profiles`).

4. **Mise à jour** : Après modification des filtres, relancez le moissonnage pour appliquer les nouveaux critères.

---

## Débogage

### Vérifier les filtres appliqués

```bash
# Récupérer la configuration d'une source
curl "http://localhost:5000/api/3/action/harvest_source_show?id=source-id" \
  -H "Authorization: YOUR_API_KEY" | jq '.result.config'
```

### Tester les filtres CQL

Vous pouvez tester les filtres CQL directement sur le serveur CSW :

```bash
curl "https://catalogue.example.com/csw?service=CSW&version=2.0.2&request=GetRecords&typeNames=csw:Record&resultType=results&constraintLanguage=CQL_TEXT&constraint_language_version=1.1.0&constraint=dc:type%20=%20'dataset'" \
  -H "Content-Type: application/xml"
```

---

## Conseils

1. **Commencez simple** : Testez d'abord avec un filtre simple (ex: `dc:type = 'dataset'`), puis complexifiez progressivement.

2. **Utilisez CQL si possible** : CQL est plus simple à écrire que OGC Filter XML.

3. **Documentez vos filtres** : Ajoutez des commentaires dans le champ "Description" de la source pour expliquer les critères de filtrage.

4. **Surveillez les résultats** : Après le moissonnage, vérifiez que les datasets importés correspondent bien à vos critères.
