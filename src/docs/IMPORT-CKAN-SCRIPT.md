# Script d'import CKAN - Documentation

## Fonctionnalités

Le script `import-from-ckan.py` permet d'importer **complètement** toutes les données d'un CKAN source vers un CKAN cible :

### 1. Organisations
- Récupère toutes les organisations avec leurs métadonnées complètes
- Importe les extras, tags, et configurations
- Préserve les IDs via un mapping

### 2. Utilisateurs
- Récupère tous les utilisateurs avec leurs profils complets
- Importe les informations (nom, email, fullname, about, sysadmin, etc.)
- **Note** : Les mots de passe ne peuvent pas être importés (sécurité)

### 3. Datasets (Packages)
- Récupère tous les datasets (y compris privés et drafts)
- Importe toutes les métadonnées (title, notes, author, license, etc.)
- Préserve les tags, groupes, extras
- Préserve les métadonnées de harvest (harvest_source_id, etc.)
- Mappe correctement les organisations propriétaires

### 4. Ressources (avec fichiers)
- **IMPORTANT** : Le script télécharge et upload les fichiers réels
- Pour les ressources avec `url_type='upload'` :
  - Télécharge le fichier depuis le CKAN source
  - Upload le fichier dans le storage CKAN de destination
  - Crée la ressource avec le fichier uploadé
- Pour les ressources avec URL externe :
  - Transforme les URLs internes (Kubernetes) en URLs publiques
  - Crée la ressource avec l'URL externe

### 5. Activités (historique)
- Récupère l'historique d'activité de chaque dataset
- **Sauvegarde dans des fichiers JSON** pour archive :
  - Un fichier par dataset : `import-activities-YYYYMMDD-HHMMSS/dataset-{id}.json`
  - Format JSON avec toutes les métadonnées d'activité
  - Inclut : activity_type, timestamp, user_id, data, etc.
- **Note** : Les activités ne peuvent pas être importées directement dans CKAN (limitation API), mais sont sauvegardées pour archive

## Utilisation

### Rendre le script exécutable

```bash
chmod +x import-from-ckan.py
```

### Exécution basique

```bash
./import-from-ckan.py \
  --source-url https://source.ckan.fr \
  --source-token YOUR_SOURCE_TOKEN \
  --target-url https://target.ckan.fr \
  --target-token YOUR_TARGET_TOKEN
```

### Avec variables d'environnement

```bash
export SOURCE_CKAN_URL=https://source.ckan.fr
export SOURCE_CKAN_TOKEN=YOUR_TOKEN
export TARGET_CKAN_URL=https://target.ckan.fr
export TARGET_CKAN_TOKEN=YOUR_TOKEN

./import-from-ckan.py
```

### Mode dry-run (simulation)

```bash
./import-from-ckan.py --dry-run --source-url ... --target-url ...
```

### Options avancées

```bash
# Importer seulement les datasets (sans users/orgs)
./import-from-ckan.py --skip-users --skip-organizations ...

# Transformer les URLs internes Kubernetes
./import-from-ckan.py \
  --source-public-url https://data.metropole-dijon.fr \
  --namespace-url-mapping '{"dijon-bfc":"https://data.metropole-dijon.fr"}' \
  ...

# Désactiver l'import des activités
./import-from-ckan.py --no-import-activities ...
```

## Structure des fichiers générés

### Logs
- `import-ckan-YYYYMMDD-HHMMSS.log` : Log complet de l'import

### Activités (si activé)
- `import-activities-YYYYMMDD-HHMMSS/` : Dossier contenant les activités
  - `dataset-{id}.json` : Activités de chaque dataset au format JSON

### Fichiers temporaires
- Dossier temporaire pour les fichiers téléchargés (nettoyé automatiquement)

## Vérification de la complétude

Le script récupère **tout** :

### Organisations
- Nom, titre, description
- Image URL
- Extras
- Tags
- Type, état, statut d'approbation
- Utilisateurs membres (via organization_show avec include_users)

### Utilisateurs
- Nom, email, fullname
- About, sysadmin status
- État (active/inactive)
- Mots de passe : **non importés** (sécurité CKAN)

### Datasets
- Toutes les métadonnées (title, notes, author, etc.)
- Tags complets
- Groupes
- Extras (incluant harvest_source_id, etc.)
- Licence
- État (active/deleted)
- Visibilité (private/public)
- Version
- Organisation propriétaire (mappée)

### Ressources
- **Fichiers réels téléchargés et uploadés** (pour url_type='upload')
- Métadonnées complètes (name, description, format, mimetype, size, hash)
- Dates (created, last_modified)
- Position
- Extras
- URLs transformées (Kubernetes → publiques)

### Activités
- Récupérées pour chaque dataset
- Sauvegardées en JSON pour archive
- Non importées dans CKAN (limitation API)

## Gestion des erreurs

Le script gère les erreurs de manière robuste :

1. **Erreurs API** : Loggées avec détails, comptées dans les stats
2. **Erreurs de téléchargement** : Fallback vers URL externe si échec
3. **Fichiers manquants** : Création avec URL externe si fichier introuvable
4. **Erreurs de mapping** : Warnings loggés, import continue
5. **Interruption** : Stats affichées avant sortie

### Statistiques affichées

À la fin de l'import, le script affiche :
- Nombre d'éléments importés
- Nombre d'éléments ignorés (déjà existants)
- Nombre d'erreurs
- Nombre de datasets moissonnés
- Nombre d'activités récupérées

## Transformation des URLs

Le script transforme automatiquement les URLs internes Kubernetes en URLs publiques :

### Pattern détecté
- `http://data4citizen.{namespace}.svc.cluster.local:8080/...`
- Autres patterns `.svc.cluster.local`

### Mapping configurable
- Via `--namespace-url-mapping` : `{"dijon-bfc":"https://data.metropole-dijon.fr"}`
- Via fichier JSON : `--namespace-url-mapping-file mapping.json`
- Par défaut : `{namespace}.data.example.org`

## Limitations connues

1. **Mots de passe utilisateurs** : Non importés (sécurité CKAN)
2. **Activités** : Récupérées et sauvegardées en JSON, mais non importées dans CKAN (limitation API)
3. **Permissions** : Les permissions spécifiques ne sont pas importées (utiliser les rôles d'organisation)
4. **Relations complexes** : Certaines relations avancées peuvent nécessiter un import manuel

## Exemple de sortie

```
Début de l'import complet
============================================================

Étape 1: Import des organisations
------------------------------------------------------------
15 organisations trouvées
Organisation créée: org1 (ID: abc-123)
 Organisation 'org2' existe déjà, ignorée
...

Étape 2: Import des utilisateurs
------------------------------------------------------------
42 utilisateurs trouvés
Utilisateur créé: user1 (ID: def-456)
...

Étape 3: Import des datasets
------------------------------------------------------------
123 datasets trouvés
Dataset créé: dataset1 (ID: ghi-789)
  Téléchargement du fichier depuis: https://...
  Fichier téléchargé: 12345 bytes
  Ressource créée: resource1
    Fichier uploadé avec succès
  Historique récupéré pour le dataset: 5 activités
  Activités sauvegardées: import-activities-20241216-120000/dataset-ghi-789.json
...

============================================================
Statistiques d'import
============================================================

Organizations:
  Importés: 12
   Ignorés: 3
  Erreurs: 0

Users:
  Importés: 38
   Ignorés: 4
  Erreurs: 0

Datasets:
  Importés: 120
   Ignorés: 3
  Erreurs: 0
  Moissonnés: 45

Resources:
  Importés: 456
   Ignorés: 0
  Erreurs: 2

Activities:
  Activités récupérées: 1234

Fichiers d'activités sauvegardés: 120 fichiers dans import-activities-20241216-120000
   Exemple: dataset-ghi-789.json

Import terminé avec succès!
```

## Dépannage

### Erreur "Permission denied"
- Vérifier que les tokens API ont les permissions admin
- Vérifier que les URLs sont accessibles

### Erreur "File not found"
- Vérifier que les fichiers existent sur le CKAN source
- Vérifier les permissions de lecture sur les fichiers

### Erreur "Upload failed"
- Vérifier la taille des fichiers (limite de timeout)
- Vérifier les permissions d'écriture sur le CKAN cible
- Vérifier l'espace disque disponible

### URLs non transformées
- Vérifier le mapping des namespaces
- Vérifier que `--source-public-url` est correct











