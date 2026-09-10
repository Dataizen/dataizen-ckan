# ckanext-custom-licenses

Extension CKAN pour charger les licences depuis un fichier JSON personnalisé.

## Description

Cette extension permet de charger les licences CKAN depuis un fichier JSON personnalisé au lieu d'utiliser les licences par défaut de CKAN. Elle surcharge l'action `license_list` pour charger les licences depuis le fichier `/srv/app/config_files/common/licenses.json`.

## Installation

L'extension est déjà installée dans l'image Docker. Pour l'activer, ajoutez `custom_licenses` à la liste des plugins dans `ckan.ini` :

```ini
ckan.plugins = ... custom_licenses
```

## Configuration

Par défaut, l'extension charge les licences depuis `/srv/app/config_files/common/licenses.json`. Vous pouvez changer ce chemin en ajoutant dans `ckan.ini` :

```ini
ckan.custom_licenses_file = /chemin/vers/votre/fichier/licenses.json
```

## Format du fichier JSON

Le fichier JSON doit contenir un tableau d'objets licence. Chaque licence doit avoir les propriétés suivantes :

```json
{
  "id": "identifiant-unique",
  "title": "Nom de la licence",
  "url": "URL vers la licence",
  "domain_content": true/false,
  "domain_data": true/false,
  "domain_software": true/false,
  "family": "",
  "is_generic": true/false,
  "od_conformance": "approved" | "not reviewed",
  "osd_conformance": "approved" | "not reviewed",
  "maintainer": "",
  "status": "active"
}
```

## Licences incluses

Le fichier par défaut inclut :
- Licence Ouverte Version 2.0 (LOV2) - Licence française pour l'open data
- Creative Commons CCZero (cc-zero)
- Creative Commons Attribution (cc-by)
- Creative Commons Attribution Share-Alike (cc-by-sa)
- Open Data Commons (ODC-PDDL, ODC-ODbL, ODC-BY)
- Et d'autres licences courantes

## Fonctionnement

L'extension surcharge l'action `license_list` de CKAN. Quand CKAN demande la liste des licences (par exemple dans le formulaire de création de dataset), l'extension charge les licences depuis le fichier JSON au lieu d'utiliser les licences par défaut de CKAN.

Si le fichier JSON n'existe pas ou s'il y a une erreur lors du chargement, l'extension utilise les licences par défaut de CKAN comme fallback.

## Édition des licences

Vous pouvez éditer le fichier `/srv/app/config_files/common/licenses.json` pour :
- Ajouter de nouvelles licences
- Modifier les informations des licences existantes
- Supprimer des licences non désirées

Après modification, redémarrez CKAN pour que les changements prennent effet.






