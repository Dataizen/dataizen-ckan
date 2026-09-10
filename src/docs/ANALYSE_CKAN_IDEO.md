# Analyse du code CKAN de production (ckan_IDEO)

## Résumé exécutif

Ce document analyse le code du CKAN de production (`https://trouver.ternum-bfc.fr`) pour identifier les éléments réutilisables dans notre instance Dataizen.

---

## Extensions et Plugins CKAN

### Extensions utilisées en production

1. **`ckanext-cas`** (SSO CAS)
   - Authentification centralisée via CAS
   - Mapping utilisateur : `email~email user~username fullname~first_name+last_name sysadmin~is_superuser`
   - Single Sign-Out activé
   - **Nous utilisons Keycloak à la place** (équivalent fonctionnel)

2. **`ckanext-dcat`** (DCAT/RDF)
   - Interface DCAT pour l'interopérabilité
   - Endpoints RDF activés : `ckanext.dcat.enable_rdf_endpoints = True`
   - **À intégrer** : Support DCAT pour conformité avec les standards européens

3. **`ckanext-geoview`** (Vues géospatiales)
   - Formats supportés : `wms kml`
   - Basemaps personnalisés : `basemaps.json`
   - Forward OGC params : `ckanext.geoview.ol_viewer.forward_ogc_request_params = True`
   - Feature hover activé
   - **Déjà présent** dans notre setup

4. **`ckanext-spatial`** (Recherche spatiale)
   - Backend Solr pour recherche spatiale
   - SRID 4326 (WGS84)
   - Carte commune personnalisée (OpenStreetMap)
   - **Déjà présent** dans notre setup

5. **`ckanext-scheming`** (Schémas personnalisés)
   - Schémas de datasets personnalisés
   - Presets pour les champs
   - **À évaluer** : Besoin de schémas personnalisés ?

6. **`ckanext-restricted`** (Accès restreint)
   - Gestion des ressources privées
   - URL de login alternatif
   - **Utile** pour gérer les données sensibles

7. **`ckanext-showcase`** (Showcases)
   - Création de "showcases" pour mettre en avant des datasets
   - **Intéressant** pour la curation de contenu

8. **`ckanext-idgotheme`** (Thème personnalisé)
   - Thème spécifique IDéO
   - Intégration Matomo (analytics)
   - Liens vers WordPress, extracteur, dataviz
   - Extent géographique : `2.00 46.00 7.20 48.50` (Bourgogne-Franche-Comté)
   - **Thème spécifique** : À adapter pour Dataizen

9. **`ckanext-composite`** (Datasets composites)
   - Permet de créer des datasets composites
   - **Utile** pour agréger plusieurs ressources

10. **`ckanext-structured-data`** (Structured Data)
    - Données structurées pour SEO
    - **Intéressant** pour le référencement

### Plugins de vue

- `image_view` - Visualisation d'images
- `text_view` - Visualisation texte
- `recline_view` - Visualisation Recline (tableau interactif)
- `recline_graph_view` - Graphiques Recline
- `recline_grid_view` - Grille Recline
- `recline_map_view` - Carte Recline
- `geo_view` - Vue géospatiale (OpenLayers)
- `geojson_view` - Vue GeoJSON
- `wmts_view` - Vue WMTS
- `pdf_view` - Visualisation PDF
- `webpage_view` - Visualisation de pages web

**Vues par défaut** : `image_view text_view recline_view wmts_view geojson_view pdf_view`

---

## Configuration Géospatiale

### Configuration geoview

```ini
ckanext.geoview.ol_viewer.formats = wms kml
ckanext.geoview.basemaps = %(here)s/basemaps.json
ckanext.geoview.ol_viewer.default_feature_hoveron = True
ckanext.geoview.ol_viewer.forward_ogc_request_params = True
```

### Configuration spatial

```ini
ckanext.spatial.search_backend = solr
ckan.spatial.srid = 4326
ckanext.spatial.common_map.type = custom
ckanext.spatial.common_map.custom.url = https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png
ckanext.spatial.common_map.attribution = &copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors
```

### Basemaps personnalisés (`basemaps.json`)

- OSM (OpenStreetMap)
- Stamen Watercolor
- Stamen Terrain
- Stamen Toner
- OSM-FR

**À réutiliser** : Le fichier `basemaps.json` pour avoir plusieurs options de fonds de carte

---

## Configuration Datastore

```ini
ckan.datastore.write_url = postgresql://...
ckan.datastore.read_url = postgresql://...
ckan.datastore.default_fts_lang = french
ckan.datastore.default_fts_index_method = gist
ckan.datastore.sqlsearch.enabled = True
```

**Points intéressants** :
- Recherche full-text en français (`french`)
- Index GIST pour performances
- SQLSearch activé pour requêtes SQL directes

**À appliquer** : Configuration FTS française si pas déjà fait

---

## Configuration Datapusher

```ini
ckan.datapusher.formats = csv xls xlsx tsv application/csv application/vnd.ms-excel application/vnd.openxmlformats-officedocument.spreadsheetml.sheet
ckan.datapusher.url = http://{{ host }}:8800/
```

**Nous utilisons xloader** : Équivalent mais plus moderne

---

## Authentification et Autorisation

### Configuration CAS (remplacé par Keycloak chez nous)

```ini
ckanext.cas.user_mapping = email~email user~username fullname~first_name+last_name sysadmin~is_superuser
ckanext.cas.single_sign_out = True
ckanext.cas.login_checkup_time = 10
```

### Autorisations

```ini
ckan.auth.anon_create_dataset = false
ckan.auth.create_unowned_dataset = false
ckan.auth.create_dataset_if_not_in_organization = false
ckan.auth.user_create_groups = false
ckan.auth.user_create_organizations = false
ckan.auth.user_delete_groups = true
ckan.auth.user_delete_organizations = false
ckan.auth.create_user_via_api = true
ckan.auth.create_user_via_web = true
ckan.auth.roles_that_cascade_to_sub_groups = admin
```

**À vérifier** : Ces règles d'autorisation sont-elles adaptées à notre usage ?

---

## OGC et Services Géospatiaux

### Configuration OGC dans geoview

```ini
ckanext.geoview.ol_viewer.formats = wms kml
ckanext.geoview.ol_viewer.forward_ogc_request_params = True
```

**Ce qui manque dans notre setup actuel** :
- Pas de configuration explicite pour afficher les liens OGC sur les pages de ressources
- Pas de template `ogc_services.html` dans le thème de production
- **Nous avons créé** `ogc_services.html` dans `ckanext-ogc` mais il faut vérifier qu'il s'affiche

**Ce que fait la production** :
- Affiche des liens WMS/WFS par organisation (`/maps/<org_id>`)
- Utilise probablement `ckanext-idgotheme` pour injecter ces liens dans les templates

**À faire** : Vérifier que notre template `ogc_services.html` s'affiche correctement sur les pages de ressources géospatiales

---

## Configuration Resource Proxy

```ini
ckan.resource_proxy.max_file_size = 10737418240  # 10 Go
```

**À vérifier** : Notre limite actuelle est-elle suffisante ?

---

## Activité et Tracking

```ini
ckan.activity_streams_enabled = true
ckan.activity_list_limit = 31
ckan.activity_streams_email_notifications = true
ckan.email_notifications_since = 2 days
ckan.hide_activity_from_users = ckan_idgo, openpaca, %(ckan.site_id)s
```

**Scripts cron** :
- `cron_ckan_rebuild_solr_index.sh` - Rebuild index Solr
- `cron_ckan_tracking_update.sh` - Mise à jour tracking + rebuild index
- `cron_ckan_email_notifications.sh` - Notifications email

**À intégrer** : Scripts cron pour maintenance automatique

---

## Internationalisation

```ini
ckan.locale_default = fr
ckan.locale_order = en pt_BR ja it cs_CZ ca es fr el sv sr sr@latin no sk fi ru de pl nl bg ko_KR hu sa sl lv
ckan.locales_filtered_out = en_GB
```

**Déjà configuré** : Français par défaut

---

## Recherche et Indexation

```ini
ckan.search.rows_max = 2000
ckan.extra_resource_fields = datatype,support,granularity
```

**Points intéressants** :
- Limite de résultats augmentée à 2000
- Champs supplémentaires pour les ressources (`datatype`, `support`, `granularity`)

**À vérifier** : Ces champs supplémentaires sont-ils utiles pour notre cas ?

---

## Configuration Thème (IDéO)

```ini
ckanext.idgotheme.name = IDéO
ckanext.idgotheme.url_site_wp = https://{{wordpress}}
ckanext.idgotheme.url_site_publier = https://{{ckan_admin_domain}}
ckanext.idgotheme.url_site_extracteur = https://{{ckan_admin_domain}}/extractor
ckanext.idgotheme.url_rawgraphs = https://{{domain_prefix}}dataviz.ternum-bfc.fr
ckanext.idgotheme.extent = 2.00 46.00 7.20 48.50
ckanext.idgotheme.matomo_site_url = {{ckanext_matomo_site_url}}
ckanext.idgotheme.matomo_site_id = {{ckanext_matomo_site_id}}
```

**Liens utiles** :
- Site WordPress
- Site de publication (admin)
- Extracteur de données
- Dataviz (RawGraphs)

**Spécifique au thème IDéO** : À adapter si on utilise un thème personnalisé

---

## Docker et Infrastructure

### Dockerfile

- Utilise `redhat/ubi8:8.1` (pas recommandé pour nous, on utilise Ubuntu/Debian)
- Installation Python 2 (obsolète)
- Virtualenv dans `/WEBS/ternum/ckan.ternum.fr/docs/neogeo-ckan`

### Build Process

- Script `build.sh` pour installer les dépendances
- Script `build_package_versions.sh` pour versionner les packages
- Configuration via Jinja2 templates (`production.ini.j2`)

**Intéressant** : Système de configuration via templates Jinja2 avec variables d'environnement

---

## Scripts Utiles

### 1. `cron_ckan_rebuild_solr_index.sh`
```bash
paster --plugin=ckan search-index rebuild --config=production.ini
```

### 2. `cron_ckan_tracking_update.sh`
```bash
paster --plugin=ckan tracking update -c production.ini
paster --plugin=ckan search-index rebuild -r -c production.ini
```

### 3. `cron_ckan_email_notifications.sh`
(Non disponible dans le code fourni)

**À adapter** : Ces scripts utilisent `paster` (ancien), à convertir en `ckan` CLI

---

## Recommandations Prioritaires

### Priorité Haute

1. **Intégrer `ckanext-dcat`**
   - Pour conformité DCAT/RDF
   - Standards européens

2. **Vérifier l'affichage des services OGC**
   - S'assurer que `ogc_services.html` s'affiche correctement
   - Tester les liens WMS/WFS par organisation

3. **Ajouter les scripts cron de maintenance**
   - Rebuild index Solr
   - Tracking update
   - Adaptés pour `ckan` CLI (pas `paster`)

4. **Configuration FTS française**
   - `ckan.datastore.default_fts_lang = french`

### Priorité Moyenne

5. **Fichier `basemaps.json`**
   - Ajouter plusieurs options de fonds de carte

6. **Limite de recherche**
   - `ckan.search.rows_max = 2000`

7. **Champs supplémentaires ressources**
   - `ckan.extra_resource_fields = datatype,support,granularity`

8. **Configuration Resource Proxy**
   - Vérifier `ckan.resource_proxy.max_file_size`

### Priorité Basse

9. **Évaluer `ckanext-scheming`**
   - Si besoin de schémas personnalisés

10. **Évaluer `ckanext-showcase`**
    - Pour curation de contenu

11. **Évaluer `ckanext-composite`**
    - Pour datasets composites

12. **Évaluer `ckanext-structured-data`**
    - Pour SEO

---

## Fichiers à Réutiliser

1. `config_files/common/basemaps.json` - Basemaps personnalisés
2. Configuration géospatiale (geoview, spatial)
3. Scripts cron (adaptés pour `ckan` CLI)
4. Templates Jinja2 (si système de configuration similaire)

---

## Points d'Attention

1. **Production utilise Python 2** - Obsolète, nous utilisons Python 3
2. **CAS vs Keycloak** - Équivalent fonctionnel mais différentes implémentations
3. **Thème IDéO** - Spécifique, nécessiterait adaptation
4. **Configuration via templates** - Système intéressant mais peut nécessiter refactoring

---

## Conclusion

Le code de production contient plusieurs éléments intéressants à réutiliser, notamment :
- Configuration géospatiale avancée
- Scripts de maintenance
- Extensions utiles (DCAT, Restricted, Showcase)
- Configuration Datastore optimisée

Les éléments les plus facilement réutilisables sont les configurations (geoview, spatial, datastore) et les scripts cron (après adaptation pour `ckan` CLI).

