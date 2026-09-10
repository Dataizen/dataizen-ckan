# tests-e2e — Tests d'ingestion bout-en-bout

Suite pytest qui pousse des jeux de données réels (CSV, GeoJSON,
shapefile) dans une instance CKAN locale et vérifie le bon
fonctionnement de la chaîne xloader / datapusher / datagis / mapfile /
pygeoapi.

## Pré-requis

Une stack `dataizen-ckan` qui tourne en local :

```bash
./start-local.sh
# Vérifier : curl http://localhost:8080/api/3/action/status_show
```

Dépendances Python :

```bash
pip install -r tests-e2e/requirements.txt
```

## Lancer

Depuis la racine du repo :

```bash
make test-e2e           # tout
pytest tests-e2e/ -v    # équivalent direct
```

Lancer un seul module :

```bash
pytest tests-e2e/test_csv_ingest.py -v
pytest tests-e2e/test_geojson_ingest.py::test_geojson_resource_is_recognized -v
```

## Configuration

Variables d'environnement (avec valeurs par défaut) :

| Variable             | Défaut                                      | Rôle                                  |
|----------------------|---------------------------------------------|---------------------------------------|
| `CKAN_URL`           | `http://localhost:8080`                     | URL CKAN cible                        |
| `CKAN_API_KEY`       | `ckan-local-dev-apikey`      | Token admin                           |
| `CKAN_E2E_ORG`       | `e2e-tests`                                 | Organisation utilisée (créée au besoin)|
| `CKAN_E2E_TIMEOUT`   | `180`                                       | Timeout polling chargements (s)       |
| `CKAN_E2E_POLL`      | `3`                                         | Intervalle de polling (s)             |

Exemple pour pointer sur la qualif :

```bash
CKAN_URL=https://ckan2.qualif-data.example.org \
CKAN_API_KEY=<token> \
CKAN_E2E_ORG=dataizen-dev \
pytest tests-e2e/ -v
```

## Couverture

| Module                       | Cas testés                                              |
|------------------------------|---------------------------------------------------------|
| `test_csv_ingest.py`         | CSV simple → datastore, CSV lat/lon → `the_geom`        |
| `test_geojson_ingest.py`     | Point / LineString / Polygon → datagis + flag spatial   |
| `test_shapefile_ingest.py`   | Shapefile zip (3 points BFC) → datagis + flag spatial   |
| `test_ogc_services.py`       | pygeoapi root, MapServer WMS GetCapabilities, proxies CKAN |

Les datasets créés sont purgés à la fin de chaque test
(`dataset_purge`) ; l'organisation `e2e-tests` est conservée pour
factoriser les setups.

## Limitations connues

- Suppose un user admin (token `CKAN_API_KEY`) qui peut créer une
  organisation et y publier des jeux de données.
- Le shapefile est généré à la volée avec `pyshp` pour éviter de
  versionner un binaire ; le test est `skip` si la lib est absente.
- Les tests OGC sont des smoke tests (HTTP 200 + payload non vide), pas
  une validation de schéma XML.
- En cas d'échec, les ressources créées sont nettoyées même si le test
  a planté. L'organisation `e2e-tests` est conservée entre runs.
