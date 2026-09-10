# CKAN OGC Extension - Avec pygeoapi Intégré

## **Architecture Complète**

Le plugin OGC démarre automatiquement pygeoapi et synchronise les datasets géospatiaux.

## **Structure**

```
ckanext-ogc/ckanext/ogc/
├── __init__.py      # Obligatoire Python
├── plugin.py        # Plugin complet avec pygeoapi intégré
└── templates/
    └── ogc_services.html  # Lien vers pygeoapi
```

## **Fonctionnalités**

### 1. **Démarrage automatique de pygeoapi**
- Démarre pygeoapi sur le port 5001 au démarrage de CKAN
- Vérifie que le service est opérationnel
- Gestion des erreurs de démarrage

### 2. **Synchronisation automatique**
- Synchronise tous les datasets géospatiaux au démarrage
- Détecte automatiquement les datasets avec données géospatiales
- Met à jour la configuration pygeoapi

### 3. **Hooks de synchronisation**
- **Création de dataset** → Synchronisation automatique
- **Modification de dataset** → Synchronisation automatique  
- **Suppression de dataset** → Suppression de la config
- **Modification de ressource** → Synchronisation du dataset parent

### 4. **Redémarrage intelligent**
- Redémarre pygeoapi après synchronisation (car il ne relit pas sa config à chaud)
- Gestion stable des redémarrages
- Vérification de la disponibilité du service

## **Configuration**

### Détection géospatiale
Le plugin détecte automatiquement les datasets géospatiaux :
- **Formats** : GeoJSON, Shapefile, KML, KMZ, GeoPackage
- **Datastore actif** : CSV avec coordonnées géographiques

### Configuration pygeoapi
- **Fichier** : `/srv/app/pygeoapi/local.config.yml`
- **Provider** : CKAN Provider intégré
- **API Key** : `ckan-local-dev-apikey`
- **URL CKAN** : `http://localhost:5000`

## **URLs**

- **CKAN** : http://localhost:8080
- **pygeoapi** : http://localhost:5001
- **OGC API** : http://localhost:5001/api
- **Collections** : http://localhost:5001/collections

## **Processus de synchronisation**

1. **Démarrage CKAN** → Plugin OGC se charge
2. **Démarrage pygeoapi** → Service OGC démarre
3. **Attente pygeoapi** → Vérification de disponibilité
4. **Scan datasets** → Détection des datasets géospatiaux
5. **Mise à jour config** → Ajout des collections à pygeoapi
6. **Redémarrage pygeoapi** → Chargement de la nouvelle config
7. **Services OGC actifs** → WFS, WMS, WMTS disponibles

## **Synchronisation en temps réel**

### Ajout de dataset
```
Dataset créé → Détection géospatiale → Ajout à config → Redémarrage pygeoapi
```

### Modification de dataset
```
Dataset modifié → Re-synchronisation → Mise à jour config → Redémarrage pygeoapi
```

### Suppression de dataset
```
Dataset supprimé → Suppression de config → Redémarrage pygeoapi
```

## **Stabilité Production**

- **Threading asynchrone** : Synchronisation non-bloquante
- **Gestion d'erreurs** : Logs détaillés et récupération d'erreurs
- **Verrouillage** : Évite les démarrages multiples
- **Timeouts** : Protection contre les blocages
- **Redémarrage robuste** : Gestion propre des processus

## **Avantages**

1. **Automatique** : Aucune intervention manuelle nécessaire
2. **Temps réel** : Synchronisation immédiate des changements
3. **Stable** : Gestion robuste des erreurs et redémarrages
4. **Production** : Prêt pour un environnement de production
5. **Standards** : Conformité OGC API native

## **Logs**

```bash
# Voir les logs du plugin OGC
docker compose logs ckan | grep "OGC Plugin"

# Voir les logs pygeoapi
docker compose logs ckan | grep "pygeoapi"
```

## **Débogage**

```bash
# Tester pygeoapi
curl http://localhost:5001/api

# Vérifier les collections
curl http://localhost:5001/collections

# Accéder au conteneur
docker compose exec ckan bash
```