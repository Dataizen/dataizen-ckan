# OGC Services - Workflow manuel

## Objectif

Ce système fournit des services OGC (WFS, WMS, WMTS) pour tous les datasets géospatiaux de CKAN. La synchronisation est maintenant **manuelle** pour éviter les problèmes de timing avec le démarrage de CKAN.

## Workflow recommandé

### 1. **Démarrage des services**
```bash
# Démarre pygeoapi et CKAN
./start-services.sh
```

### 2. **Vérification des fichiers**
```bash
# Vérifie que tous les fichiers sont présents
./check-files.sh
```

### 3. **Attendre que CKAN soit prêt**
- Vérifiez que CKAN est accessible : http://localhost:5000
- Attendez que tous les services soient démarrés

### 4. **Synchronisation OGC manuelle**
```bash
# Lance la synchronisation OGC
./sync-ogc-manual.sh
```

### 5. **Tests**
```bash
# Test de tous les datasets
./test-all-datasets.sh

# Test des problèmes spécifiques
./test-specific-issues.sh
```

## Fichiers créés

### Scripts de synchronisation
- `sync-ogc-manual.sh` : Synchronisation manuelle OGC
- `check-files.sh` : Vérification des fichiers
- `test-all-datasets.sh` : Test de tous les datasets
- `test-specific-issues.sh` : Test des problèmes spécifiques

### Scripts Python
- `add_ogc_links.py` : Ajoute les liens OGC
- `fix_config.py` : Corrige la configuration
- `force_update_links.py` : Force la mise à jour des liens
- `update_all_collections.py` : Met à jour tous les datasets
- `diagnostic.py` : Diagnostic des problèmes

### Providers OGC
- `CKANProvider` : Provider Feature (GeoJSON)
- `WFSProvider` : Provider WFS (XML GML)
- `WMSProvider` : Provider WMS (Cartes)
- `WMTSProvider` : Provider WMTS (Tuiles)

## Configuration automatique

Chaque dataset géospatial aura automatiquement :

### Liens de service
- **WFS** : `/collections/{dataset}/wfs`
- **WMS** : `/collections/{dataset}/wms`
- **WMTS** : `/collections/{dataset}/wmts`

### Liens de téléchargement
- **Shapefile** : `/collections/{dataset}/items?f=shp`
- **GeoPackage** : `/collections/{dataset}/items?f=gpkg`

### Providers
- **Feature** : GeoJSON
- **WFS** : XML GML
- **WMS** : Cartes
- **WMTS** : Tuiles

## Tests disponibles

### Test complet
```bash
./test-all-datasets.sh
```
Vérifie pour chaque dataset :
- Liens OGC présents
- Endpoints accessibles
- Données disponibles
- Items individuels accessibles

### Test spécifique
```bash
./test-specific-issues.sh
```
Vérifie les problèmes spécifiques :
- Erreurs CRS corrigées
- Méthode `get()` fonctionnelle
- Configuration correcte

## Diagnostic

### Vérification des fichiers
```bash
./check-files.sh
```
Vérifie que tous les fichiers requis sont présents.

### Diagnostic détaillé
```bash
python3 /srv/app/pygeoapi-providers/ckan_provider/diagnostic.py
```
Diagnostic complet de la connectivité et des données.

## Résultat attendu

Après synchronisation, tous vos datasets géospatiaux auront :

1. **Liens OGC visibles** sur les pages de collection
2. **Endpoints fonctionnels** pour WFS, WMS, WMTS
3. **Téléchargements** Shapefile et GeoPackage
4. **Items individuels** accessibles
5. **Logs propres** sans erreurs

## Notes importantes

- **Timing** : Attendez que CKAN soit complètement démarré avant la synchronisation
- **Chemins** : Les scripts utilisent `/srv/app/` comme base
- **Permissions** : Assurez-vous que les scripts sont exécutables
- **Connectivité** : Vérifiez que CKAN est accessible avant la synchronisation

## 🆘 Dépannage

### Fichiers manquants
```bash
./check-files.sh
```

### Problèmes de connectivité
```bash
curl http://localhost:5000/api/action/site_read
```

### Logs détaillés
```bash
python3 /srv/app/pygeoapi-providers/ckan_provider/diagnostic.py
```

### Réinitialisation
```bash
./sync-ogc-manual.sh
```

