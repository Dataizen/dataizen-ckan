# ckanext-admin-tools

Extension CKAN pour les outils d'administration avancés.

## Fonctionnalités

### 1. Suppression en masse
- **Datasets** : Suppression en masse de datasets avec sélection multiple
- **Organisations** : Suppression en masse d'organisations
- **Membres** : Suppression en masse de membres d'une organisation

### 2. Synchronisations
- **pygeoapi** : Lancement de la synchronisation complète des collections pygeoapi
- **MapServer** : Génération des mapfiles MapServer pour tous les datasets géospatiaux

### 3. État des moissonnages
- Visualisation des jobs de moissonnage en cours
- Progression détaillée par job
- Liste des sources actives

## Installation

L'extension est installée automatiquement lors du build Docker.

Pour l'installer manuellement :

```bash
pip install -e /srv/app/ckanext-admin-tools
```

## Configuration

Ajouter `admin_tools` à la liste des plugins dans `ckan.ini` :

```ini
ckan.plugins = ... admin_tools
```

## Utilisation

### Accès aux pages admin

- **Suppression en masse** : `/admin-tools/bulk-delete`
- **Synchronisations** : `/admin-tools/sync`
- **État des moissonnages** : `/admin-tools/harvest-status`

### API Actions

#### `admin_bulk_delete_datasets`
Supprime plusieurs datasets en masse.

```python
{
    "dataset_ids": ["dataset1", "dataset2", ...]
}
```

#### `admin_bulk_delete_organizations`
Supprime plusieurs organisations en masse.

```python
{
    "organization_ids": ["org1", "org2", ...]
}
```

#### `admin_bulk_delete_members`
Supprime plusieurs membres d'une organisation.

```python
{
    "organization_id": "org_id",
    "member_ids": ["user1", "user2", ...]
}
```

#### `admin_sync_pygeoapi`
Lance la synchronisation pygeoapi complète.

#### `admin_sync_mapserver`
Lance la génération des mapfiles MapServer.

#### `admin_get_harvest_status`
Récupère l'état des moissonnages en cours.

## Sécurité

Toutes les actions nécessitent les droits d'administrateur système (`sysadmin`).

## Notes

- La poubelle CKAN fonctionne correctement grâce au correctif dans `ckanext-ogc` pour gérer les entités de type `file`.
- Les synchronisations peuvent prendre du temps selon le nombre de datasets.

