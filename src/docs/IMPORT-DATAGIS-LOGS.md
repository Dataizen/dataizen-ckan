# Logs de l'import datagis

## Vue d'ensemble

L'import vers datagis se fait **à deux moments** :

1. **Au démarrage de CKAN** : Import de toutes les ressources géospatiales non-datastore
2. **Via hook `after_resource_create`** : Import automatique lors de la création d'une ressource géospatiale

---

## 1. Import au démarrage

### Quand ?

L'import au démarrage se fait dans `_schedule_delayed_startup()` après :
- La synchronisation pygeoapi
- L'attente que CKAN soit complètement prêt

### Logs attendus

```
================================================================================
 IMPORT DES FICHIERS GÉOSPATIAUX DANS DATAGIS (DÉMARRAGE)
================================================================================
Scan des ressources géospatiales non-datastore...
Script trouvé: /usr/local/bin/import-geospatial-to-datagis.py
Paramètres:
   - CKAN URL: http://localhost:5000
   - Datagis DB: datagis
   - PostGIS Host: db:5432
   - Storage Path: /var/lib/ckan/default
Import datagis réussi (code: 0)
Résumé de l'import:
   Traitement ressource abc123: fichier.shp (SHP)
   Importé: res_abc123
   5/10 ressources importées dans datagis
================================================================================
Import datagis terminé au démarrage
================================================================================
```

### Vérifier les logs

```bash
# Voir les logs CKAN
docker compose logs ckan | grep -i "datagis\|import.*géospatial"

# Voir les logs détaillés
docker compose logs ckan | grep -A 20 "IMPORT DES FICHIERS GÉOSPATIAUX"
```

### Si l'import échoue

Les logs indiqueront :
- Si le script n'est pas trouvé
- Si la base datagis n'existe pas
- Si les paramètres sont incorrects
- Les erreurs détaillées du script

---

## 2. Import via hook (création de ressource)

### Quand ?

L'import se déclenche automatiquement dans `after_resource_create()` si :
- Le format est géospatial (SHP, GPKG, GeoJSON, etc.)
- `datastore_active = False`

### Logs attendus

```
================================================================================
 IMPORT AUTOMATIQUE DATAGIS (HOOK after_resource_create)
   Ressource ID: abc123-def456-ghi789
   Nom: mon_fichier.shp
   Format: SHP
   MIME type: application/zip
   ZIP détecté: True
   Datastore active: False
================================================================================
Lancement import datagis pour ressource abc123-def456-ghi789...
Import datagis réussi pour ressource abc123-def456-ghi789
   DÉBUT IMPORT RESSOURCE DANS DATAGIS
   Nom de table généré: res_abc123_def456_ghi789
    DÉBUT IMPORT DATAGIS VIA OGR2OGR
   IMPORT DATAGIS RÉUSSI
   Table: res_abc123_def456_ghi789
```

### Vérifier les logs

```bash
# Voir les logs d'import pour une ressource spécifique
docker compose logs ckan | grep -A 30 "IMPORT AUTOMATIQUE DATAGIS"

# Voir les logs d'une ressource spécifique
docker compose logs ckan | grep "abc123-def456-ghi789"
```

### Délai d'attente

Le hook attend **2 secondes** avant de lancer l'import pour s'assurer que le fichier est complètement uploadé :

```python
# Attendre un peu pour que le fichier soit complètement uploadé
time.sleep(2)
```

---

## 3. Logs du script import-geospatial-to-datagis.py

Le script lui-même génère des logs détaillés qui sont capturés et remontés dans les logs CKAN.

### Niveaux de log

- **INFO** : Progression normale
- **WARNING** : Problèmes non-bloquants (table existe déjà, fichier non trouvé)
- **ERROR** : Erreurs bloquantes (base n'existe pas, erreur ogr2ogr)

### Marqueurs dans les logs

- `` : Traitement d'une ressource
- `` : Succès
- `` : Erreur
- `` : Avertissement
- `` : Opération géospatiale
- `` : Information

---

## 4. Vérifier que l'import fonctionne

### Méthode 1 : Vérifier les logs

```bash
# Voir tous les imports réussis
docker compose logs ckan | grep "Import datagis réussi"

# Voir les imports échoués
docker compose logs ckan | grep "Import datagis échoué"

# Compter les ressources importées
docker compose logs ckan | grep "ressources importées dans datagis"
```

### Méthode 2 : Vérifier dans la base datagis

```bash
# Se connecter à la base datagis
docker compose exec db psql -U ckan -d datagis

# Lister les tables créées
\dt res_*

# Voir les détails d'une table
\d res_abc123_def456_ghi789

# Compter les tables
SELECT COUNT(*) FROM information_schema.tables 
WHERE table_schema = 'public' AND table_name LIKE 'res_%';
```

### Méthode 3 : Vérifier via le script

```bash
# Lancer le script manuellement avec logs détaillés
docker compose exec ckan python3 /usr/local/bin/import-geospatial-to-datagis.py --resource-id {resource_id}
```

---

## 5. Problèmes courants et solutions

### Problème : "Script import datagis non trouvé"

**Solution** :
```bash
# Vérifier que le script existe
docker compose exec ckan ls -la /usr/local/bin/import-geospatial-to-datagis.py

# Si absent, vérifier le chemin dans la config
docker compose exec ckan env | grep IMPORT_DATAGIS_SCRIPT
```

### Problème : "Base de données 'datagis' n'existe pas"

**Solution** :
```bash
# Créer la base datagis
docker compose exec db psql -U ckan -c "CREATE DATABASE datagis OWNER ckan ENCODING 'utf-8';"

# Activer PostGIS
docker compose exec db psql -U ckan -d datagis -c "CREATE EXTENSION IF NOT EXISTS postgis;"
```

### Problème : "Fichier source introuvable"

**Solution** :
- Vérifier que le fichier est bien uploadé dans `ckan_storage/resources/`
- Vérifier les permissions du fichier
- Vérifier le chemin de storage dans la config CKAN

### Problème : "Timeout lors de l'import"

**Solution** :
- Les fichiers très volumineux peuvent prendre du temps
- Le timeout est de 5 minutes pour un hook, 10 minutes au démarrage
- Vérifier les logs pour voir où ça bloque

---

## 6. Désactiver l'import au démarrage

Si vous ne voulez pas importer au démarrage (par exemple si vous utilisez `start-local.sh --todatagis`), vous pouvez commenter la section dans `plugin.py` :

```python
# Import des fichiers géospatiaux dans datagis
# try:
#     log.info("Import des fichiers géospatiaux dans datagis...")
#     self._import_geospatial_to_datagis()
#     log.info("Import datagis terminé")
# except Exception as e:
#     log.error(f"Erreur import datagis: {e}")
```

L'import via hook continuera de fonctionner.

---

## Résumé

**Import au démarrage** : OUI, bien logué avec marqueurs visibles  
**Import via hook** : OUI, déclenché automatiquement à la création de ressource  
**Logs détaillés** : OUI, avec marqueurs (, , , , )  
**Vérification facile** : Via logs CKAN ou requêtes SQL dans datagis
