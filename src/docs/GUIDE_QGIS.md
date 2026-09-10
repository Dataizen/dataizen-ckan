# Guide d'utilisation des services géospatiaux dans QGIS

## Services WMS (Web Map Service)

### Option 1 : Accès direct à MapServer (Recommandé)

Pour un dataset spécifique, utilisez l'URL suivante dans QGIS :

```
http://localhost:8081/wms?map=/mapserver/mapfiles/{dataset_name}.map
```

**Exemple concret** :
```
http://localhost:8081/wms?map=/mapserver/mapfiles/adresses-des-etablissements-daccueil-du-jeune-enfant-percevant-une-prestation-de-service-caf-et-nom.map
```

**Dans QGIS** :
1. Menu `Couche` → `Ajouter une couche` → `Ajouter une couche WMS/WMTS`
2. Cliquez sur `Nouveau` pour créer une nouvelle connexion
3. **Nom** : Donnez un nom à la connexion (ex: "Établissements d'accueil")
4. **URL** : Collez l'URL ci-dessus
5. Cliquez sur `OK`, puis `Connecter`
6. Sélectionnez la couche dans la liste et cliquez sur `Ajouter`

### Option 2 : Toutes les couches d'une organisation (Nouveau !)

Pour voir **toutes les couches** d'une organisation dans QGIS, utilisez :

```
http://localhost:8080/wms/{org_id}?service=WMS&version=1.3.0&request=GetCapabilities
```

**Exemple** :
```
http://localhost:8080/wms/agence-regionale-du-numerique-et-de-lintelligence-artificielle-arnia?service=WMS&version=1.3.0&request=GetCapabilities
```

**Dans QGIS** :
1. Menu `Couche` → `Ajouter une couche` → `Ajouter une couche WMS/WMTS`
2. Cliquez sur `Nouveau` pour créer une nouvelle connexion
3. **Nom** : Donnez un nom à la connexion (ex: "ARNiA - Toutes les couches")
4. **URL** : Collez l'URL ci-dessus (sans les paramètres `service`, `version`, `request`)
   - Utilisez juste : `http://localhost:8080/wms/{org_id}`
5. Cliquez sur `OK`, puis `Connecter`
6. **Toutes les couches de l'organisation** apparaîtront dans la liste !
7. Sélectionnez les couches souhaitées et cliquez sur `Ajouter`

**Note importante** : Pour les requêtes GetMap individuelles, vous devrez toujours spécifier le paramètre `layers` avec le nom du dataset.

### Option 3 : Via le proxy CKAN (Alternative)

Le proxy CKAN nécessite de spécifier le dataset dans l'URL :

```
http://localhost:8080/wms/{org_id}?layers={dataset_name}
```

**Exemple** :
```
http://localhost:8080/wms/agence-regionale-du-numerique-et-de-lintelligence-artificielle-arnia?layers=adresses-des-etablissements-daccueil-du-jeune-enfant-percevant-une-prestation-de-service-caf-et-nom
```

## Services WFS (Web Feature Service)

### Accès direct à MapServer

Pour charger les features (géométries + attributs) dans QGIS :

```
http://localhost:8081/wfs?map=/mapserver/mapfiles/{dataset_name}.map
```

**Exemple** :
```
http://localhost:8081/wfs?map=/mapserver/mapfiles/adresses-des-etablissements-daccueil-du-jeune-enfant-percevant-une-prestation-de-service-caf-et-nom.map
```

**Dans QGIS** :
1. Menu `Couche` → `Ajouter une couche` → `Ajouter une couche WFS`
2. Cliquez sur `Nouveau` pour créer une nouvelle connexion
3. **Nom** : Donnez un nom à la connexion
4. **URL** : Collez l'URL ci-dessus
5. Cliquez sur `OK`, puis `Connecter`
6. Sélectionnez la couche dans la liste et cliquez sur `Ajouter`

### Via le proxy CKAN

```
http://localhost:8080/maps/{org_id}?SERVICE=WFS&REQUEST=GetCapabilities&TYPENAMES={dataset_name}
```

## OGC API Features (via pygeoapi)

Pour les services OGC API modernes :

```
http://localhost:5001/collections/{collection_id}/items
```

**Exemple** :
```
http://localhost:5001/collections/adresses-des-etablissements-daccueil-du-jeune-enfant-percevant-une-prestation-de-service-caf-et-nom/items
```

**Dans QGIS** (version 3.28+) :
1. Menu `Couche` → `Ajouter une couche` → `Ajouter une couche OGC API Features`
2. **URL** : Collez l'URL de la collection (sans `/items`)
3. Cliquez sur `Connecter`
4. Sélectionnez la collection et cliquez sur `Ajouter`

## Liste des datasets disponibles

Pour obtenir la liste de tous les datasets disponibles pour une organisation :

```bash
curl "http://localhost:8080/wms/{org_id}?service=WMS&request=GetCapabilities&layers="
```

Cela retourne une liste JSON avec tous les datasets disponibles.

## Résolution des problèmes

### Erreur de symbole dans la légende

Si vous voyez une erreur comme :
```
msLoadMSRasterBufferFromFile(): unable to open file /mapserver/mapfiles/circle for reading
```

Cela signifie que le mapfile utilise un symbole qui n'existe pas. Régénérez le mapfile :

```bash
docker exec mapserver python3 /usr/local/bin/generate-mapfile.py \
  --dataset {dataset_name} \
  --ckan-url http://ckan:5000 \
  --ckan-api-key {votre_api_key} \
  --postgis-host db \
  --postgis-port 5432 \
  --postgis-db datastore \
  --postgis-user ckan \
  --postgis-password ckan \
  --mapfiles-dir /mapserver/mapfiles
```

### Erreur GetLegendGraphic

Si la légende ne s'affiche pas, assurez-vous que la requête inclut le paramètre `SLD_VERSION=1.1.0` :

```
http://localhost:8081/wms?map=/mapserver/mapfiles/{dataset}.map&SERVICE=WMS&VERSION=1.3.0&REQUEST=GetLegendGraphic&LAYER={dataset}&FORMAT=image/png&STYLE=default&SLD_VERSION=1.1.0
```

QGIS devrait automatiquement ajouter ce paramètre, mais si ce n'est pas le cas, vous pouvez le spécifier manuellement.

## Exemples d'URLs complètes

### WMS GetCapabilities (toutes les couches d'une organisation)
```
http://localhost:8080/wms/agence-regionale-du-numerique-et-de-lintelligence-artificielle-arnia?service=WMS&version=1.3.0&request=GetCapabilities
```

### WMS GetCapabilities (une couche spécifique)
```
http://localhost:8081/wms?map=/mapserver/mapfiles/adresses-des-etablissements-daccueil-du-jeune-enfant-percevant-une-prestation-de-service-caf-et-nom.map&SERVICE=WMS&VERSION=1.3.0&REQUEST=GetCapabilities
```

### WMS GetMap
```
http://localhost:8081/wms?map=/mapserver/mapfiles/adresses-des-etablissements-daccueil-du-jeune-enfant-percevant-une-prestation-de-service-caf-et-nom.map&SERVICE=WMS&VERSION=1.3.0&REQUEST=GetMap&LAYERS=adresses-des-etablissements-daccueil-du-jeune-enfant-percevant-une-prestation-de-service-caf-et-nom&CRS=EPSG:3857&BBOX=-556597,5160979,1113194,6621293&WIDTH=1014&HEIGHT=600&FORMAT=image/png
```

### WMS GetLegendGraphic
```
http://localhost:8081/wms?map=/mapserver/mapfiles/adresses-des-etablissements-daccueil-du-jeune-enfant-percevant-une-prestation-de-service-caf-et-nom.map&SERVICE=WMS&VERSION=1.3.0&REQUEST=GetLegendGraphic&LAYER=adresses-des-etablissements-daccueil-du-jeune-enfant-percevant-une-prestation-de-service-caf-et-nom&FORMAT=image/png&STYLE=default&SLD_VERSION=1.1.0
```

### WFS GetCapabilities
```
http://localhost:8081/wfs?map=/mapserver/mapfiles/adresses-des-etablissements-daccueil-du-jeune-enfant-percevant-une-prestation-de-service-caf-et-nom.map&SERVICE=WFS&VERSION=2.0.0&REQUEST=GetCapabilities
```

### WFS GetFeature
```
http://localhost:8081/wfs?map=/mapserver/mapfiles/adresses-des-etablissements-daccueil-du-jeune-enfant-percevant-une-prestation-de-service-caf-et-nom.map&SERVICE=WFS&VERSION=2.0.0&REQUEST=GetFeature&TYPENAMES=adresses-des-etablissements-daccueil-du-jeune-enfant-percevant-une-prestation-de-service-caf-et-nom&COUNT=10&outputformat=geojson
```

## Ports et services

- **CKAN** : `http://localhost:8080`
- **MapServer** : `http://localhost:8081`
- **pygeoapi** : `http://localhost:5001`

## Recommandation

**Pour QGIS, utilisez l'Option 2 (toutes les couches d'une organisation)** car :
- Vous voyez toutes les couches disponibles en une seule connexion
- L'URL est simple et standard
- QGIS peut faire ses propres requêtes GetCapabilities
- Fonctionne directement avec les outils standard de QGIS
- Pas besoin de connaître le nom exact de chaque dataset

Pour les requêtes individuelles, utilisez l'Option 1 (accès direct à MapServer) pour de meilleures performances.
