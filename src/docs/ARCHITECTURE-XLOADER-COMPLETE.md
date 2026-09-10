# Architecture XLoader - Vue d'ensemble complète

## Vue d'ensemble

XLoader est le système qui charge automatiquement les données des ressources CKAN dans le datastore PostgreSQL. Voici comment il fonctionne dans votre projet.

---

## Flux complet d'exécution

```
1. CRÉATION/MODIFICATION DE RESSOURCE
   ↓
2. xloader_submit() appelé (automatique ou manuel)
   ↓
3. Job ajouté à Redis Queue: ckan:default:default
   ↓
4. Worker RQ récupère le job
   ↓
5. Worker exécute xloader_data_into_datastore(input)
   ↓
6. Téléchargement de la ressource
   ↓
7. Chargement dans datastore PostgreSQL
   ↓
8. Mise à jour du statut dans datapusher_jobs
```

---

## Composants principaux

### 1. Worker RQ (Processus principal)

**Fichier de configuration :** `ckan-app/ckan/supervisor.d/xloader.conf`

**Commande exécutée :**
```bash
python3 /srv/app/rq_worker_wrapper.py default
```

**Ce que fait le wrapper (`rq_worker_wrapper.py`) :**
1. Attend que Redis soit disponible
2. Initialise CKAN (load_config, load_environment)
3. Vérifie que `model.Session.bind` est configuré
4. Lance `ckan jobs worker default`
5. Le worker RQ écoute la queue `ckan:default:default`

**Important :** Le wrapper initialise CKAN AVANT de lancer le worker, mais cette initialisation peut ne pas être propagée au contexte d'exécution des jobs.

---

### 2. Fonction `xloader_data_into_datastore`

**Localisation :** `/srv/app/src/ckanext-xloader/ckanext/xloader/jobs.py`

**Ce qu'elle fait :**
1. **Initialisation CKAN** (via patch `21-patch-xloader-init-ckan.sh`)
   - Vérifie si `model.Session.bind` est configuré
   - Si non, lit `ckan.ini` et configure SQLAlchemy manuellement
   - Crée l'engine et bind la session

2. **Récupération du job_id**
   ```python
   job_id = get_current_job().id
   ```

3. **Appel callback pour marquer "running"**
   ```python
   callback_xloader_hook(result_url=input['result_url'], 
                         api_key=input['api_key'], 
                         job_dict={'status': 'running'})
   ```

4. **Téléchargement de la ressource**
   - Appelle `_download_resource_data(resource_id)`
   - Utilise `get_resource_and_dataset` avec `ignore_auth=True` (patché par `15-patch-xloader-permissions.sh`)
   - Ajoute header `Authorization` si nécessaire (patché par `18-patch-xloader-auth-header.sh`)

5. **Chargement dans datastore**
   - Parse le fichier (CSV, Excel, etc.)
   - Crée/modifie la table dans le datastore
   - Insère les données

6. **Mise à jour du statut**
   - Appelle `callback_xloader_hook` avec `status='complete'` ou `'error'`

---

### 3. Patches appliqués

#### Patch 1 : Initialisation CKAN (`21-patch-xloader-init-ckan.sh`)
**Problème résolu :** `UnboundExecutionError` - CKAN non initialisé dans le contexte du job

**Solution :** Ajoute du code au début de `xloader_data_into_datastore` pour :
- Vérifier si `model.Session.bind` est configuré
- Si non, lire `ckan.ini` et configurer SQLAlchemy manuellement
- Créer l'engine et binder la session

**Code ajouté :**
```python
def _ensure_sqlalchemy_bound():
    from ckan import model
    if model.Session.bind is not None:
        return
    
    # Lire ckan.ini et configurer SQLAlchemy
    ini = os.environ.get("CKAN_INI", "/srv/app/ckan.ini")
    cp = ConfigParser()
    cp.read(ini)
    sqlalchemy_url = cp.get("app:main", "sqlalchemy.url")
    
    engine = create_engine(sqlalchemy_url, pool_pre_ping=True)
    model.meta.engine = engine
    model.Session.bind = engine

_ensure_sqlalchemy_bound()
```

#### Patch 2 : Permissions (`15-patch-xloader-permissions.sh`)
**Problème résolu :** Erreur 403 lors du téléchargement de la ressource

**Solution :** Modifie `get_resource_and_dataset` pour utiliser `ignore_auth=True`

#### Patch 3 : Headers d'authentification (`18-patch-xloader-auth-header.sh`)
**Problème résolu :** Erreur 401 lors du téléchargement de ressources protégées

**Solution :** Ajoute le header `Authorization` aux requêtes HTTP

#### Patch 4 : Noms de colonnes (`16-patch-xloader-column-names.sh`)
**Problème résolu :** Noms de colonnes invalides (caractères spéciaux)

**Solution :** Normalise les noms de colonnes avant création de la table

---

## Points de défaillance possibles

### 1. **Worker ne démarre pas**

**Symptômes :**
- `supervisorctl status xloader` → `FATAL` ou `EXITED`
- Pas de logs dans `/dev/stdout`

**Causes possibles :**
- Redis non accessible
- Erreur de syntaxe dans `rq_worker_wrapper.py`
- Erreur d'initialisation CKAN dans le wrapper
- Binaire `ckan` non trouvé ou non exécutable

**Diagnostic :**
```bash
# Vérifier les logs supervisor
supervisorctl tail -100 xloader

# Vérifier Redis
timeout 2 bash -lc "cat < /dev/null > /dev/tcp/redis/6379" && echo "Redis OK" || echo "Redis inaccessible"

# Tester le wrapper manuellement
python3 /srv/app/rq_worker_wrapper.py default
```

---

### 2. **Worker démarre mais plante immédiatement**

**Symptômes :**
- Worker démarre puis s'arrête en < 30 secondes
- `supervisorctl status xloader` → `EXITED` puis redémarre
- Logs montrent "has started" puis "has stopped"

**Causes possibles :**
- **UnboundExecutionError** : CKAN non initialisé dans le contexte du job
  - Le patch `21-patch-xloader-init-ckan.sh` n'est pas appliqué
  - Le patch est appliqué mais échoue silencieusement
- **Erreur de syntaxe** dans `jobs.py` après patch
- **Import error** : Module Python non trouvé
- **Erreur de connexion** à PostgreSQL/Redis

**Diagnostic :**
```bash
# Vérifier que le patch est appliqué
grep -A 5 "Patched by databfc: ensure CKAN SQLAlchemy is bound" \
  /srv/app/src/ckanext-xloader/ckanext/xloader/jobs.py

# Vérifier la syntaxe
python3 -m py_compile /srv/app/src/ckanext-xloader/ckanext/xloader/jobs.py

# Vérifier les logs d'erreur
tail -200 /dev/stderr | grep -E "ERROR|Exception|Traceback|UnboundExecutionError"
```

---

### 3. **Job soumis mais non traité**

**Symptômes :**
- Job reste dans la queue Redis
- `datastore_active: false`
- Pas de logs de traitement

**Causes possibles :**
- Worker non démarré ou crashé
- Queue incorrecte (job dans `ckan:default:default` mais worker écoute autre chose)
- Redis inaccessible au worker

**Diagnostic :**
```bash
# Vérifier les queues Redis
python3 << 'PYTHON'
import redis
r = redis.Redis(host='redis', port=6379, db=1, decode_responses=False)
queues = [key.decode('utf-8') for key in r.keys('rq:queue:*')]
for queue in sorted(queues):
    length = r.llen(queue)
    print(f"{queue}: {length} jobs")
PYTHON

# Vérifier le statut du worker
supervisorctl status xloader

# Vérifier les jobs en attente
python3 << 'PYTHON'
import redis
r = redis.Redis(host='redis', port=6379, db=1, decode_responses=False)
# Vérifier les jobs dans la queue default
queue_key = b'rq:queue:ckan:default:default'
jobs = r.lrange(queue_key, 0, -1)
print(f"Jobs en attente: {len(jobs)}")
for i, job_id in enumerate(jobs[:5]):
    print(f"  Job {i+1}: {job_id.decode('utf-8')[:50]}...")
PYTHON
```

---

### 4. **Job traité mais échoue silencieusement**

**Symptômes :**
- Job retiré de la queue (traité)
- `datastore_active: false`
- Pas d'erreur visible dans les logs
- `xloader_status: error` dans les extras de la ressource

**Causes possibles :**
- **UnboundExecutionError** : CKAN non initialisé (patch non appliqué ou échoue)
- **Erreur HTTP 403/401** : Problème d'authentification lors du téléchargement
- **Erreur de parsing** : Fichier CSV/Excel malformé
- **Erreur de connexion datastore** : PostgreSQL non accessible
- **Timeout** : Fichier trop volumineux

**Diagnostic :**
```bash
# Vérifier le statut xloader de la ressource
RESOURCE_ID="votre-resource-id"
API_KEY="${CKAN_API_KEY}"

curl -s -H "Authorization: $API_KEY" \
  "http://localhost:5000/api/action/resource_show?id=$RESOURCE_ID" | \
  python3 -c "
import sys, json
r = json.load(sys.stdin)['result']
print(f\"datastore_active: {r.get('datastore_active', False)}\")
for e in r.get('extras', []):
    if 'xloader' in e.get('key', '').lower():
        print(f\"{e.get('key')}: {e.get('value')}\")
"

# Vérifier les logs récents pour cette ressource
tail -500 /dev/stdout | grep -E "$RESOURCE_ID|ERROR|Exception|Traceback" | tail -20

# Vérifier les task_status dans datapusher_jobs
psql -h db -U datapusher -d datapusher_jobs -c \
  "SELECT entity_id, state, error, last_updated FROM task_status \
   WHERE entity_id = '$RESOURCE_ID' ORDER BY last_updated DESC LIMIT 1;"
```

---

### 5. **Erreur lors du téléchargement de la ressource**

**Symptômes :**
- Logs montrent "403 Forbidden" ou "401 Unauthorized"
- `xloader_error` contient "HTTP Error" ou "Permission denied"

**Causes possibles :**
- Patch `15-patch-xloader-permissions.sh` non appliqué
- Patch `18-patch-xloader-auth-header.sh` non appliqué
- `api_key` invalide ou expiré
- URL de la ressource inaccessible

**Diagnostic :**
```bash
# Vérifier les patches
grep -q "ignore_auth=True" /srv/app/src/ckanext-xloader/ckanext/xloader/jobs.py && \
  echo "Patch permissions appliqué" || echo "Patch permissions NON appliqué"

grep -q "Patched by databfc: add Authorization header" \
  /srv/app/src/ckanext-xloader/ckanext/xloader/jobs.py && \
  echo "Patch auth header appliqué" || echo "Patch auth header NON appliqué"

# Tester le téléchargement manuellement
RESOURCE_ID="votre-resource-id"
API_KEY="${CKAN_API_KEY}"

# Récupérer l'URL de la ressource
RESOURCE_URL=$(curl -s -H "Authorization: $API_KEY" \
  "http://localhost:5000/api/action/resource_show?id=$RESOURCE_ID" | \
  python3 -c "import sys, json; print(json.load(sys.stdin)['result']['url'])")

# Tester le téléchargement
curl -I -H "Authorization: $API_KEY" "$RESOURCE_URL"
```

---

## Commandes de diagnostic complètes

### Vérifier l'état global du système

```bash
echo "=== 1. Statut Supervisor ==="
supervisorctl status xloader

echo ""
echo "=== 2. Queues Redis ==="
python3 << 'PYTHON'
import redis
r = redis.Redis(host='redis', port=6379, db=1, decode_responses=False)
queues = sorted([k.decode('utf-8') for k in r.keys('rq:queue:*')])
for queue in queues:
    length = r.llen(queue)
    print(f"{queue}: {length} jobs")
PYTHON

echo ""
echo "=== 3. Patches appliqués ==="
echo -n "Patch init CKAN: "
grep -q "Patched by databfc: ensure CKAN SQLAlchemy is bound" \
  /srv/app/src/ckanext-xloader/ckanext/xloader/jobs.py && \
  echo "" || echo ""

echo -n "Patch permissions: "
grep -q "ignore_auth=True" \
  /srv/app/src/ckanext-xloader/ckanext/xloader/jobs.py && \
  echo "" || echo ""

echo -n "Patch auth header: "
grep -q "Patched by databfc: add Authorization header" \
  /srv/app/src/ckanext-xloader/ckanext/xloader/jobs.py && \
  echo "" || echo ""

echo ""
echo "=== 4. Logs récents (erreurs) ==="
tail -100 /dev/stderr | grep -E "ERROR|Exception|Traceback|UnboundExecutionError" | tail -10
```

### Tester une ressource spécifique

```bash
RESOURCE_ID="votre-resource-id"
API_KEY="${CKAN_API_KEY}"

echo "=== Test ressource: $RESOURCE_ID ==="

# 1. Statut actuel
echo "1. Statut actuel:"
curl -s -H "Authorization: $API_KEY" \
  "http://localhost:5000/api/action/resource_show?id=$RESOURCE_ID" | \
  python3 -c "
import sys, json
r = json.load(sys.stdin)['result']
print(f\"  datastore_active: {r.get('datastore_active', False)}\")
print(f\"  format: {r.get('format')}\")
print(f\"  url_type: {r.get('url_type')}\")
"

# 2. Soumettre à xloader
echo "2. Soumission à xloader:"
curl -s -X POST -H "Authorization: $API_KEY" \
  -H "Content-Type: application/json" \
  -d "{\"resource_id\": \"$RESOURCE_ID\"}" \
  "http://localhost:5000/api/action/xloader_submit" | python3 -m json.tool

# 3. Attendre 10 secondes
echo "3. Attente 10 secondes..."
sleep 10

# 4. Vérifier le statut final
echo "4. Statut final:"
curl -s -H "Authorization: $API_KEY" \
  "http://localhost:5000/api/action/resource_show?id=$RESOURCE_ID" | \
  python3 -c "
import sys, json
r = json.load(sys.stdin)['result']
print(f\"  datastore_active: {r.get('datastore_active', False)}\")
for e in r.get('extras', []):
    if 'xloader' in e.get('key', '').lower():
        print(f\"  {e.get('key')}: {e.get('value')}\")
"
```

---

## Checklist de diagnostic

Quand xloader ne fonctionne pas, vérifier dans cet ordre :

- [ ] **1. Worker est-il démarré ?**
  ```bash
  supervisorctl status xloader
  ```

- [ ] **2. Redis est-il accessible ?**
  ```bash
  timeout 2 bash -lc "cat < /dev/null > /dev/tcp/redis/6379" && echo "" || echo ""
  ```

- [ ] **3. Les patches sont-ils appliqués ?**
  ```bash
  grep -q "Patched by databfc" /srv/app/src/ckanext-xloader/ckanext/xloader/jobs.py && echo "" || echo ""
  ```

- [ ] **4. Y a-t-il des jobs en attente ?**
  ```bash
  python3 -c "import redis; r=redis.Redis(host='redis', port=6379, db=1); print(len(r.lrange('rq:queue:ckan:default:default', 0, -1)))"
  ```

- [ ] **5. Y a-t-il des erreurs dans les logs ?**
  ```bash
  tail -200 /dev/stderr | grep -E "ERROR|Exception|Traceback"
  ```

- [ ] **6. Le patch d'initialisation CKAN fonctionne-t-il ?**
  ```bash
  # Vérifier que le code du patch est présent et syntaxiquement correct
  python3 -m py_compile /srv/app/src/ckanext-xloader/ckanext/xloader/jobs.py && echo "" || echo ""
  ```

---

## Solutions aux problèmes courants

### Problème : UnboundExecutionError

**Solution :** Vérifier que le patch `21-patch-xloader-init-ckan.sh` est appliqué et s'exécute avec sudo :

```bash
# Vérifier le patch
grep -A 30 "Patched by databfc: ensure CKAN SQLAlchemy is bound" \
  /srv/app/src/ckanext-xloader/ckanext/xloader/jobs.py

# Si non présent, appliquer manuellement
bash /docker-entrypoint.d/21-patch-xloader-init-ckan.sh
```

### Problème : Worker plante immédiatement

**Solution :** Vérifier les logs et la syntaxe :

```bash
# Voir les logs détaillés
supervisorctl tail -f xloader

# Vérifier la syntaxe
python3 -m py_compile /srv/app/src/ckanext-xloader/ckanext/xloader/jobs.py

# Tester le wrapper manuellement
python3 /srv/app/rq_worker_wrapper.py default
```

### Problème : Job reste en queue

**Solution :** Vérifier que le worker écoute la bonne queue :

```bash
# Vérifier les queues
python3 << 'PYTHON'
import redis
r = redis.Redis(host='redis', port=6379, db=1, decode_responses=False)
queues = sorted([k.decode('utf-8') for k in r.keys('rq:queue:*')])
for queue in queues:
    length = r.llen(queue)
    print(f"{queue}: {length} jobs")
PYTHON

# Redémarrer le worker
supervisorctl restart xloader
```

---

## Fichiers clés à consulter

- **Configuration worker :** `ckan-app/ckan/supervisor.d/xloader.conf`
- **Wrapper worker :** `ckan-app/ckan/rq_worker_wrapper.py`
- **Fonction principale :** `/srv/app/src/ckanext-xloader/ckanext/xloader/jobs.py`
- **Patches :**
  - `ckan-app/ckan/docker-entrypoint.d/21-patch-xloader-init-ckan.sh`
  - `ckan-app/ckan/docker-entrypoint.d/15-patch-xloader-permissions.sh`
  - `ckan-app/ckan/docker-entrypoint.d/18-patch-xloader-auth-header.sh`
  - `ckan-app/ckan/docker-entrypoint.d/16-patch-xloader-column-names.sh`
- **Configuration xloader :** `ckan-app/ckan/docker-entrypoint.d/10-configure-xloader-ogc.sh`

---

## Références

- [Guide des logs XLoader](GUIDE-LOGS-XLOADER.md)
- [Workers XLoader](WORKERS-XLOADER.md)
- [Processus de dépôt de données](PROCESSUS-DEPOT-DONNEES-API.md)

