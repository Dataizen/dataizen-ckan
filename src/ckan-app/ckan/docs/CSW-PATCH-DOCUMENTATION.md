# Documentation : Patch CSW Topic Category Mapping

## Vue d'ensemble

Le script `31-patch-csw-topic-category-mapping.py` est un patch automatique qui corrige les erreurs de validation XML courantes lors du moissonnage CSW (Catalogue Service for the Web) dans CKAN. Il s'applique automatiquement au démarrage du conteneur CKAN via le système de docker-entrypoint.

**Fichier :** `ckan-app/ckan/docker-entrypoint.d/31-patch-csw-topic-category-mapping.py`

## Objectifs

1. **Correction XML avant validation** : Corriger les erreurs XML communes dans les métadonnées ISO 19139 avant la validation par CKAN
2. **Mapping français → anglais** : Convertir les codes de catégorie français en codes ISO 19139 anglais pour la validation XML
3. **Mapping anglais → français** : Convertir les codes ISO anglais en libellés français pour l'affichage dans CKAN (optionnel, contrôlé par `CSW_METADATA_LANGUAGE`)

## Problèmes résolus

### 1. TopicCategoryCode non conformes

**Problème :** Les métadonnées CSW contiennent souvent des codes de catégorie en français qui ne sont pas conformes à la norme ISO 19139 (qui exige des codes en anglais).

**Exemples d'erreurs :**
- `'limites-administratives'` → doit être `'boundaries'`
- `'transport'` → doit être `'transportation'`
- `'services'` → doit être `'society'`
- `'occupation-du-sol'` → doit être `'imageryBaseMapsEarthCover'`

**Solution :** Le patch mappe automatiquement les codes français vers les codes ISO anglais avant la validation XML.

### 2. Éléments XML vides ou invalides

**Problèmes corrigés :**
- `gco:Integer` avec valeurs vides ou invalides → supprimés
- `gco:Integer` avec attribut `nilReason` (non autorisé) → attribut supprimé
- `gco:Date` avec valeurs vides → supprimés
- `MD_TopicCategoryCode` vides → supprimés

### 3. Position incorrecte de `dateStamp`

**Problème :** L'élément `dateStamp` est parfois placé avant les éléments obligatoires (`hierarchyLevel`, `hierarchyLevelName`, `contact`) dans `MD_Metadata`, ce qui cause une erreur de validation.

**Solution :** Le patch repositionne automatiquement `dateStamp` après ces éléments obligatoires.

### 4. Format de contenu XML

**Problème :** `_validate_document()` dans CKAN attend une chaîne de caractères, mais le patch générait des bytes avec déclaration XML.

**Solution :** Le patch décode le XML en string et supprime la déclaration XML avant de passer à la validation.

## Mappings de catégories

### Mapping français → anglais (pour validation XML)

| Code français | Code ISO 19139 (anglais) |
|---------------|--------------------------|
| `limites-administratives` | `boundaries` |
| `occupation-du-sol` | `imageryBaseMapsEarthCover` |
| `services` | `society` |
| `transport` / `transports` | `transportation` |
| `urbanisme` / `cadastre` / `foncier` | `planningCadastre` |
| `eau` | `inlandWaters` |
| `environnement` | `environment` |
| `energie` / `electricite` / `reseaux` | `utilitiesCommunication` |
| `batiments` / `equipement` / `patrimoine` | `structure` |
| `culture` / `risques` | `society` |
| `economie` | `economy` |
| `localisation` | `location` |
| `contraintes` | `planningCadastre` |

### Mapping anglais → français (pour affichage CKAN)

| Code ISO 19139 | Libellé français |
|----------------|------------------|
| `boundaries` | Limites administratives |
| `imageryBaseMapsEarthCover` | Occupation du sol |
| `society` | Services |
| `transportation` | Transport |
| `structure` | Bâtiments |
| `economy` | Économie |
| `location` | Localisation |
| `environment` | Environnement |
| `inlandWaters` | Eaux intérieures |
| `planningCadastre` | Planification, cadastre |
| `utilitiesCommunication` | Réseaux, communication |

## Fonctionnement technique

### 1. Point d'injection

Le patch s'injecte dans la méthode `import_stage()` de `ckanext-spatial/ckanext/spatial/harvesters/base.py`, juste **avant** l'appel à `_validate_document()`.

### 2. Processus de correction

```python
# 1. Parse le XML depuis harvest_object.content
root = etree.fromstring(harvest_object.content)

# 2. Applique les corrections :
#    - Mapping TopicCategoryCode FR → ISO
#    - Suppression des Integer/Date vides
#    - Repositionnement de dateStamp
#    - Nettoyage des attributs invalides

# 3. Réencode en string (sans déclaration XML)
harvest_object.content = xml_str

# 4. La validation se fait ensuite normalement
is_valid, profile, errors = self._validate_document(...)
```

### 3. Mapping français (optionnel)

Si `CSW_METADATA_LANGUAGE=fr` (par défaut), le patch applique également un mapping inverse après `_transform_to_ckan()` pour convertir les codes ISO anglais en libellés français dans les `extras` du `package_dict`.

## Configuration

### Variables d'environnement

| Variable | Valeur par défaut | Description |
|----------|-------------------|-------------|
| `CSW_METADATA_LANGUAGE` | `fr` | Langue pour l'affichage des catégories dans CKAN (`fr` ou `en`) |
| `FORCE_CSW_PATCH_REAPPLY` | `false` | Force la réapplication du patch même s'il est déjà présent |

### Exemple de configuration

```bash
# Dans docker-compose.yml ou .env
CSW_METADATA_LANGUAGE=fr  # Afficher les libellés en français
FORCE_CSW_PATCH_REAPPLY=false  # Ne pas forcer la réapplication
```

## Exécution

### Démarrage automatique

Le script s'exécute automatiquement au démarrage du conteneur CKAN via :
- `ckan-app/ckan/docker-entrypoint.d/31-csw-patch.sh` (wrapper shell)
- Le script vérifie un fichier de marqueur pour éviter les exécutions multiples

### Exécution manuelle

```bash
# Dans le conteneur CKAN
python3 /docker-entrypoint.d/31-patch-csw-topic-category-mapping.py

# Avec réapplication forcée
FORCE_CSW_PATCH_REAPPLY=true python3 /docker-entrypoint.d/31-patch-csw-topic-category-mapping.py
```

## Logs et diagnostic

### Messages de log

Le patch génère des logs pour suivre son exécution :

```
[CSW PATCH] Script exécuté
[CSW PATCH] TopicCategoryCode: 'limites-administratives' -> 'boundaries'
[CSW PATCH] dateStamp repositionné dans MD_Metadata
[CSW PATCH] XML corrigé (len=10784)
[CSW PATCH] Patch appliqué avec succès
```

### Vérification du patch

Pour vérifier que le patch est bien appliqué :

```bash
# Vérifier la présence du patch dans base.py
grep -A 5 "Patch: correction des erreurs de validation XML" \
  /srv/app/src/ckanext-spatial/ckanext/spatial/harvesters/base.py

# Vérifier la syntaxe Python
python3 -m py_compile \
  /srv/app/src/ckanext-spatial/ckanext/spatial/harvesters/base.py
```

## Caractéristiques techniques

### Idempotence

Le patch est **idempotent** : il peut être exécuté plusieurs fois sans créer de duplications. Le script :
- Détecte si le patch est déjà présent
- Nettoie les anciennes versions avant réapplication
- Valide la syntaxe Python avant d'écrire le fichier

### Sécurité

- **Validation de syntaxe** : Le patch compile le fichier modifié avec `py_compile` avant de l'écrire
- **Écriture atomique** : Utilise un fichier temporaire puis `mv` pour éviter la corruption
- **Gestion des permissions** : Utilise `sudo mv` si nécessaire pour les fichiers protégés

### Robustesse

- **Gestion d'erreurs** : Toutes les opérations sont dans des blocs `try/except`
- **Fallbacks** : Si une correction échoue, le processus continue avec les autres corrections
- **Logs détaillés** : Chaque étape est loggée pour faciliter le débogage

## Dépannage

### Le patch ne s'applique pas

**Symptôme :** Les erreurs de validation XML persistent.

**Solutions :**
1. Vérifier que le script s'exécute au démarrage :
   ```bash
   docker compose logs ckan | grep "CSW PATCH"
   ```

2. Vérifier que le fichier cible existe :
   ```bash
   ls -la /srv/app/src/ckanext-spatial/ckanext/spatial/harvesters/base.py
   ```

3. Forcer la réapplication :
   ```bash
   FORCE_CSW_PATCH_REAPPLY=true python3 /docker-entrypoint.d/31-patch-csw-topic-category-mapping.py
   ```

### Erreur de syntaxe Python

**Symptôme :** `SyntaxError` ou `IndentationError` dans `base.py`.

**Solutions :**
1. Le script valide la syntaxe avant d'écrire, mais si une erreur persiste :
   ```bash
   # Vérifier la syntaxe
   python3 -m py_compile /srv/app/src/ckanext-spatial/ckanext/spatial/harvesters/base.py
   
   # Restaurer depuis l'image Docker si nécessaire
   docker compose exec ckan git checkout /srv/app/src/ckanext-spatial/ckanext/spatial/harvesters/base.py
   ```

2. Forcer la réapplication pour nettoyer les duplications :
   ```bash
   FORCE_CSW_PATCH_REAPPLY=true python3 /docker-entrypoint.d/31-patch-csw-topic-category-mapping.py
   ```

### Les libellés français n'apparaissent pas

**Symptôme :** Les catégories s'affichent en anglais dans CKAN.

**Solutions :**
1. Vérifier la variable d'environnement :
   ```bash
   echo $CSW_METADATA_LANGUAGE  # Doit être "fr"
   ```

2. Vérifier que le patch français est appliqué :
   ```bash
   grep "Patch: Conversion des TopicCategoryCode" \
     /srv/app/src/ckanext-spatial/ckanext/spatial/harvesters/base.py
   ```

3. Redémarrer CKAN après modification de `CSW_METADATA_LANGUAGE`

### Erreur "Unicode strings with encoding declaration"

**Symptôme :** `Error parsing ISO document: Unicode strings with encoding declaration are not supported`

**Solution :** Cette erreur est corrigée dans le patch. Le XML est maintenant décodé en string et la déclaration XML est supprimée avant la validation.

## Structure du code

### Fonctions principales

- `_read(path)` : Lit le fichier `base.py`
- `_write_atomic(path, content)` : Écrit le fichier avec validation de syntaxe
- `_remove_existing_patch(content)` : Supprime les anciennes versions du patch
- `_is_patch_present_once(content)` : Vérifie si le patch est déjà présent
- `_find_import_stage_block(content)` : Trouve le bloc `import_stage()` dans `base.py`
- `_indent_block(block, indent)` : Ajuste l'indentation du code injecté
- `main()` : Fonction principale qui orchestre l'application du patch

### Blocs de code injectés

1. **`PATCH_XML_BLOCK`** : Corrections XML avant validation
   - Mapping TopicCategoryCode
   - Nettoyage des éléments vides
   - Repositionnement de dateStamp

2. **`FRENCH_MAPPING_BLOCK`** : Conversion des codes en libellés français
   - Appliqué après `_transform_to_ckan()`
   - Modifie les `extras` du `package_dict`

## Exemples d'utilisation

### Exemple 1 : Correction automatique d'un TopicCategoryCode

**Avant (XML) :**
```xml
<gmd:MD_TopicCategoryCode>limites-administratives</gmd:MD_TopicCategoryCode>
```

**Après (XML) :**
```xml
<gmd:MD_TopicCategoryCode>boundaries</gmd:MD_TopicCategoryCode>
```

**Dans CKAN (si `CSW_METADATA_LANGUAGE=fr`) :**
```json
{
  "key": "spatial_topic_category",
  "value": "Limites administratives"
}
```

### Exemple 2 : Repositionnement de dateStamp

**Avant (XML invalide) :**
```xml
<gmd:MD_Metadata>
  <gmd:dateStamp>
    <gco:Date>2026-01-20</gco:Date>
  </gmd:dateStamp>
  <gmd:hierarchyLevel>...</gmd:hierarchyLevel>
  <gmd:contact>...</gmd:contact>
</gmd:MD_Metadata>
```

**Après (XML valide) :**
```xml
<gmd:MD_Metadata>
  <gmd:hierarchyLevel>...</gmd:hierarchyLevel>
  <gmd:contact>...</gmd:contact>
  <gmd:dateStamp>
    <gco:Date>2026-01-20</gco:Date>
  </gmd:dateStamp>
</gmd:MD_Metadata>
```

## Limitations

1. **Nouveaux codes français** : Si un nouveau code français apparaît qui n'est pas dans le mapping, il ne sera pas converti automatiquement. Il faut ajouter le mapping dans `topic_category_mapping`.

2. **Structure XML complexe** : Le patch gère les cas courants, mais des structures XML très complexes ou non standardes peuvent nécessiter des corrections manuelles.

3. **Performance** : Le patch parse et modifie le XML pour chaque dataset moissonné, ce qui peut ralentir légèrement le moissonnage pour de très gros volumes.

## Maintenance

### Ajouter un nouveau mapping

Pour ajouter un nouveau mapping français → anglais :

1. Modifier `31-patch-csw-topic-category-mapping.py`
2. Ajouter l'entrée dans `topic_category_mapping` (ligne ~144)
3. Ajouter l'entrée dans `topic_category_french_labels` (ligne ~307) si nécessaire
4. Redémarrer CKAN pour appliquer les changements

### Mettre à jour le patch

Si le patch doit être modifié :

1. Modifier le script
2. Forcer la réapplication :
   ```bash
   FORCE_CSW_PATCH_REAPPLY=true python3 /docker-entrypoint.d/31-patch-csw-topic-category-mapping.py
   ```

## Références

- **ISO 19139** : Schéma XML pour les métadonnées géographiques
- **CKAN Spatial Extension** : `ckanext-spatial` pour le moissonnage CSW
- **Documentation CKAN Harvesting** : https://docs.ckan.org/en/latest/maintaining/data-store.html#harvesting

## Historique des corrections

- **2026-01-20** : Correction de l'erreur `TypeError: cannot use a string pattern on a bytes-like object`
- **2026-01-20** : Ajout du repositionnement de `dateStamp` dans `MD_Metadata`
- **2026-01-18** : Correction de la sérialisation XML (bytes → string)
- **2026-01-12** : Ajout des mappings pour `risques` et `contraintes`
- **2026-01-11** : Correction de l'idempotence (éviter les duplications)
- **2026-01-10** : Correction initiale des TopicCategoryCode français
