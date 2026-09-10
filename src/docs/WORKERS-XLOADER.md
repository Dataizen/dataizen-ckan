# Workers XLoader : Explication

## Clarification importante

### Il n'y a PAS de worker séparé `xloader_data_into_datastore`

`xloader_data_into_datastore` n'est **pas un worker**, c'est une **fonction Python** qui est exécutée par le worker RQ.

## Architecture des workers

### Worker RQ unique

Il y a **un seul worker RQ** qui écoute la queue `default` (préfixée en `ckan:default:default` par CKAN) et traite **tous les types de jobs**, y compris :

1. **Jobs xloader** : `xloader_data_into_datastore`
2. **Autres jobs CKAN** : jobs de moissonnage, jobs OGC, etc.

### Configuration actuelle

**Fichier :** `supervisor.d/xloader.conf`

**Commande :**
```bash
python3 /srv/app/rq_worker_wrapper.py default
```

**Ce que ça fait :**
1. Initialise CKAN (load_config, load_environment)
2. Lance `ckan jobs worker default`
3. Le worker RQ écoute la queue `ckan:default:default`
4. Traite tous les jobs de cette queue, y compris `xloader_data_into_datastore`

## Fonction `xloader_data_into_datastore`

### C'est quoi ?

C'est la **fonction Python** qui est exécutée par le worker RQ pour charger les données dans le datastore.

**Localisation :** `ckanext-xloader/ckanext/xloader/jobs.py`

**Signature :**
```python
def xloader_data_into_datastore(input):
    """
    Fonction exécutée par le worker RQ.
    Télécharge la ressource et la charge dans le datastore PostgreSQL.
    """
```

### Comment elle est soumise à la queue ?

**Via l'action CKAN `xloader_submit` :**

```python
# Dans ckanext-xloader/ckanext/xloader/action.py
def xloader_submit(context, data_dict):
    # ...
    job = enqueue_job(
        jobs.xloader_data_into_datastore,  # ← La fonction à exécuter
        [data],
        queue='default',  # ← La queue (CKAN préfixera automatiquement)
        ...
    )
```

**Qui appelle `xloader_submit` ?**

1. **Automatiquement** : Quand une ressource est créée avec `xloader_skip=False`
2. **Manuellement** : Via l'API `xloader_submit` ou l'extension `ckanext-dataload-router`

## Flux complet

```
1. Création de ressource avec xloader_skip=False
   ↓
2. xloader_submit() est appelé (automatiquement ou manuellement)
   ↓
3. Job ajouté à Redis queue: ckan:default:default
   ↓
4. Worker RQ (ckan jobs worker default) récupère le job
   ↓
5. Worker RQ exécute xloader_data_into_datastore(input)
   ↓
6. xloader_data_into_datastore télécharge et charge dans datastore
```

## Autres workers liés à xloader ?

### Non, il n'y a qu'un seul worker RQ

Le worker RQ unique traite **tous les jobs**, pas seulement xloader :

- Jobs xloader (`xloader_data_into_datastore`)
- Jobs de moissonnage (harvest)
- Jobs OGC (si configurés)
- Autres jobs CKAN

### Pourquoi un seul worker ?

CKAN utilise un système de **queues RQ** où :
- Chaque queue peut avoir plusieurs workers
- Un worker peut écouter plusieurs queues
- Les jobs sont distribués entre les workers disponibles

**Configuration actuelle :**
- **1 worker** écoute la queue `default`
- Ce worker traite **tous les types de jobs** de cette queue

## Logs ajoutés dans le wrapper

Le wrapper `rq_worker_wrapper.py` inclut maintenant des logs détaillés pour diagnostiquer les problèmes :

### Phase 1 : Initialisation CKAN

- Vérification de l'existence de `ckan.ini`
- Tentative d'import de `_init_ckan`
- Fallback sur `load_config`/`load_environment`
- Vérification de `model.Session.bind`
- Test de connexion à la base de données
- Test d'accès à `ApiToken` (point de défaillance)

### Phase 2 : Lancement du worker

- Vérification du binaire `ckan`
- Log de la commande exécutée
- Notes sur l'héritage de l'environnement CKAN

## Diagnostic des problèmes

### Si le worker ne démarre pas

Vérifier les logs du wrapper :
```bash
tail -f /dev/stdout | grep -E "\[RQ-WORKER\]|STEP|PHASE|ERROR|CRITICAL"
```

### Si les jobs xloader échouent

Vérifier :
1. **Initialisation CKAN** : Les logs du wrapper doivent montrer `CKAN initialization completed successfully`
2. **Test ApiToken** : Les logs doivent montrer `ApiToken access successful`
3. **Logs du job** : Vérifier les logs xloader pour voir où exactement le job échoue

### Si `UnboundExecutionError` persiste

Cela signifie que :
- Soit l'initialisation CKAN n'a pas fonctionné (vérifier les logs du wrapper)
- Soit `ckan jobs worker` réinitialise CKAN et écrase notre initialisation (vérifier les logs CKAN)
- Soit RQ exécute les jobs dans un contexte isolé (nécessite une investigation plus poussée)

## Conclusion

- **1 worker RQ** : `ckan jobs worker default`
- **1 fonction job xloader** : `xloader_data_into_datastore`
- **1 queue** : `default` (préfixée en `ckan:default:default`)

Le wrapper garantit que CKAN est initialisé avant que le worker ne commence à traiter les jobs, ce qui devrait résoudre le problème `UnboundExecutionError`.





