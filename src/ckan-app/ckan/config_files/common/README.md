# Configuration des licences CKAN

Ce répertoire contient les fichiers de configuration pour CKAN, notamment le fichier de licences personnalisées.

## Fichier de licences (`licenses.json`)

Le fichier `licenses.json` contient la liste des licences disponibles dans CKAN. Ce fichier a été créé en fusionnant les licences disponibles sur :
- https://ckan2.data.example.org
- https://trouver.ternum-bfc.fr/dataset

### Édition du fichier

Vous pouvez éditer le fichier `licenses.json` pour :
- Ajouter de nouvelles licences
- Modifier les informations des licences existantes
- Supprimer des licences non désirées

### Format du fichier

Le fichier est au format JSON et contient un tableau d'objets licence. Chaque licence doit avoir les propriétés suivantes :

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
  "status": "active"
}
```

### Licences incluses

Le fichier contient actuellement les licences suivantes :

1. **Licence non spécifiée** (`notspecified`)
2. **Licence Ouverte Version 2.0** (`lov2`) - Licence française pour l'open data
3. **Creative Commons CCZero** (`cc-zero`)
4. **Creative Commons Attribution** (`cc-by`)
5. **Creative Commons Attribution Share-Alike** (`cc-by-sa`)
6. **Open Data Commons Public Domain Dedication and License** (`odc-pddl`)
7. **Open Data Commons Open Database License** (`odc-odbl`)
8. **Open Data Commons Attribution License** (`odc-by`)
9. **GNU Free Documentation License** (`gfdl`)
10. **UK Open Government Licence** (`uk-ogl`)
11. **Autre Licence (ouverte)** (`other-open`)
12. **Autre Licence (Domaine Public)** (`other-pd`)
13. **Autre Licence (spécifique)** (`other-at`)
14. **Creative Commons Non-Commercial** (`cc-nc`)
15. **Autre Licence (Non-Commercial)** (`other-nc`)
16. **Autre Licence (Non Ouverte)** (`other-closed`)

### Utilisation

Le fichier est chargé automatiquement au démarrage de CKAN via le script `70-configure-licenses.sh`.

### Références

- [Open Definition Licenses](https://opendefinition.org/licenses/)
- [Licence Ouverte 2.0](https://www.etalab.gouv.fr/licence-ouverte-open-licence)
- [CKAN License Configuration](https://docs.ckan.org/en/latest/maintaining/configuration.html#ckan-licenses-group-url)






