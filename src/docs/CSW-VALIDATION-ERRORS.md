# Erreurs de validation CSW et corrections

## Résumé des erreurs détectées

D'après les logs de moissonnage CSW, voici les principales erreurs de validation XML :

### 1. TopicCategoryCode non conformes (730 erreurs)

**Erreurs :**
- `'limites-administratives'` → doit être `'boundaries'`
- `'transport'` → doit être `'transportation'`
- `'services'` → doit être `'society'`
- `'occupation-du-sol'` → doit être `'imageryBaseMapsEarthCover'`
- Valeurs vides `''` → doivent être supprimées

**Correction :** Déjà gérée par `31-patch-csw-topic-category-mapping.py`

### 2. Integer vides (522 erreurs)

**Erreur :** `Element '{http://www.isotc211.org/2005/gco}Integer': '' is not a valid value`

**Correction :** Ajoutée dans le script amélioré - supprime les Integer vides

### 3. Date vides (14 erreurs)

**Erreur :** `Element '{http://www.isotc211.org/2005/gco}Date': '' is not a valid value`

**Correction :** Ajoutée dans le script amélioré - supprime les Date vides

### 4. TimePeriod sans endPosition (54 erreurs)

**Erreur :** `Element '{http://www.opengis.net/gml}TimePeriod': Missing child element(s). Expected is one of (endPosition, end)`

**Correction :** Ajoutée dans le script amélioré - ajoute endPosition si manquant

### 5. Éléments inattendus (15 erreurs)

**Erreur :** `Element '{http://www.fao.org/geonetwork}copy': This element is not expected`

**Correction :** Ajoutée dans le script amélioré - supprime les éléments inattendus

### 6. Éléments manquants (non corrigés automatiquement)

Ces erreurs nécessitent une connaissance de la structure XML complète et sont plus difficiles à corriger automatiquement :

- **DQ_ConformanceResult** : Missing `pass` (55 erreurs)
- **DQ_ConformanceResult** : Missing `explanation` (21 erreurs)
- **CI_Citation** : Missing `date` (21 erreurs)
- **CI_Date** : Missing `dateType` (19 erreurs)
- **MD_Format** : Missing `version` (15 erreurs)
- **date** : Élément inattendu (118 erreurs)
- **report** : Élément inattendu (57 erreurs)
- **type** : Élément inattendu (29 erreurs)
- **codeSpace** : Élément inattendu (12 erreurs)

**Solution :** Ces erreurs sont gérées par `continue_on_validation_errors = true` dans `ckan.ini` (configuré par `30-configure-harvest-validation.sh`)

## Script de correction

Le script `31-patch-csw-topic-category-mapping.py` corrige automatiquement :

1. Mapping des TopicCategoryCode non conformes
2. Suppression des TopicCategoryCode vides
3. Suppression des Integer vides
4. Suppression des Date vides
5. Suppression des éléments inattendus (comme `{http://www.fao.org/geonetwork}copy`)
6. Ajout de `endPosition` aux TimePeriod manquants

## Configuration

### Option 1 : Continuer malgré les erreurs (recommandé)

Déjà configuré par `30-configure-harvest-validation.sh` :

```ini
ckanext.spatial.harvest.continue_on_validation_errors = true
```

Cela permet d'importer les datasets même s'il y a des erreurs de validation mineures.

### Option 2 : Corriger les erreurs avant validation

Le script `31-patch-csw-topic-category-mapping.py` corrige automatiquement les erreurs les plus courantes avant la validation.

## Vérification

Pour vérifier que le patch est appliqué :

```bash
# Vérifier que le patch a été appliqué
docker compose exec ckan grep -A 5 "Patch: correction des erreurs de validation XML" /srv/app/src/ckanext-spatial/ckanext/spatial/harvesters/base.py

# Vérifier la configuration continue_on_validation_errors
docker compose exec ckan grep "continue_on_validation_errors" /srv/app/ckan.ini
```

## Re-moissonner après correction

Après avoir appliqué les corrections, vous pouvez re-moissonner les sources CSW :

```bash
# Lister les sources CSW
docker compose exec ckan ckan -c /srv/app/ckan.ini harvester sources

# Re-moissonner une source spécifique
docker compose exec ckan ckan -c /srv/app/ckan.ini harvester run <source_id>

# Ou re-moissonner toutes les sources
docker compose exec ckan ckan -c /srv/app/ckan.ini harvester run
```

## Statistiques attendues

Après application des corrections, vous devriez voir :

- **TopicCategoryCode** : Toutes les valeurs non conformes mappées
- **Integer vides** : Supprimés automatiquement
- **Date vides** : Supprimées automatiquement
- **TimePeriod** : endPosition ajouté si manquant
- **Éléments manquants** : Continuent d'être ignorés grâce à `continue_on_validation_errors = true`

Les erreurs restantes (éléments manquants) ne bloquent plus l'import grâce à `continue_on_validation_errors = true`.











