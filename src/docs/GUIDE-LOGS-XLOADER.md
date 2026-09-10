# Guide : Consulter les logs du worker xloader

## Configuration des logs

Le worker xloader est configuré dans `supervisor.d/xloader.conf` avec :
- `stdout_logfile=/dev/stdout` → Les logs vont vers la sortie standard
- `stderr_logfile=/dev/stderr` → Les erreurs vont vers la sortie d'erreur
- Logger `ckanext.xloader` en niveau `DEBUG` → Tous les logs xloader sont visibles

## 1. Logs au démarrage du worker

### Voir les logs de démarrage
```bash
# Voir les dernières lignes (démarrage)
supervisorctl tail -50 xloader

# Ou via stdout directement
tail -50 /dev/stdout | grep -E "\[XLOADER\]|xloader|worker"
```

### Ce qu'on devrait voir au démarrage
```
[XLOADER] Starting xloader worker (sans --burst, reste actif en continu)...
[xloader] waiting for redis...
[XLOADER] Redis is ready, initializing CKAN and starting worker...
Worker rq:worker:XXXXX (PID XXXX) has started
```

### Si le worker ne démarre pas
```bash
# Voir les erreurs de démarrage
supervisorctl tail -100 xloader | grep -E "ERROR|Exception|Traceback|spawn"

# Vérifier le statut
supervisorctl status xloader
```

## 2. Logs pendant le traitement d'un job

### Surveiller les logs en temps réel
```bash
# Filtrer les logs xloader
supervisorctl tail -f xloader | grep -E "\[xloader\]|xloader_data_into_datastore|CKAN|Session.bind|make_app|ERROR|Exception"

# Ou voir tous les logs du worker
supervisorctl tail -f xloader
```

### Logs ajoutés par le patch

Le patch ajoute des logs dans `xloader_data_into_datastore` :

1. **Si CKAN n'est pas initialisé** :
   ```
   WARNING [ckanext.xloader.jobs] [xloader] CKAN not initialized in job context (Session.bind is None). Bootstrapping with make_app...
   ```

2. **Si l'initialisation réussit** :
   ```
   INFO [ckanext.xloader.jobs] [xloader] CKAN initialized successfully
   ```

3. **Si l'initialisation échoue** :
   ```
   ERROR [ckanext.xloader.jobs] CKAN init failed: model.Session.bind is still None after make_app()
   ```

### Logs RQ (Redis Queue)

RQ ajoute aussi des logs pour chaque job :
```
Worker rq:worker:XXXXX (PID XXXX) has started job <Job XXXX> from queue "default"
Worker rq:worker:XXXXX (PID XXXX) has finished job <Job XXXX> from queue "default"
```

Si le job échoue :
```
Worker rq:worker:XXXXX (PID XXXX) has failed job <Job XXXX> from queue "default"
```

## 3. Commandes utiles pour diagnostiquer

### Voir les logs récents avec filtres
```bash
# Erreurs uniquement
supervisorctl tail -200 xloader | grep -E "ERROR|Exception|Traceback" | tail -30

# Logs du patch (initialisation CKAN)
supervisorctl tail -200 xloader | grep -E "\[xloader\].*CKAN|Session.bind|make_app" | tail -30

# Logs pour une ressource spécifique
RESOURCE_ID="564e43bb-7305-4562-993a-4114d6d3fa40"
supervisorctl tail -200 xloader | grep "$RESOURCE_ID" | tail -30
```

### Voir tous les logs depuis le début
```bash
# Tous les logs du worker (peut être très long)
supervisorctl tail -1000 xloader

# Ou via stdout directement
tail -1000 /dev/stdout | grep -E "xloader|XLOADER"
```

### Vérifier que le worker traite des jobs
```bash
# Surveiller les logs RQ
supervisorctl tail -f xloader | grep -E "has (started|finished|failed) job"
```

## 4. Exemple de logs complets pour un job

Voici ce qu'on devrait voir quand un job est traité avec le patch :

```
Worker rq:worker:abc123 (PID 1234) has started job <Job def456> from queue "default"
WARNING [ckanext.xloader.jobs] [xloader] CKAN not initialized in job context (Session.bind is None). Bootstrapping with make_app...
INFO [ckanext.xloader.jobs] [xloader] CKAN initialized successfully
INFO [ckanext.xloader.jobs] Starting to load resource d95e5639-95d1-4a9f-b634-6b58a0efde82
...
INFO [ckanext.xloader.jobs] Successfully loaded resource d95e5639-95d1-4a9f-b634-6b58a0efde82
Worker rq:worker:abc123 (PID 1234) has finished job <Job def456> from queue "default"
```

## 5. Si on ne voit pas de logs

### Vérifier que le worker tourne
```bash
supervisorctl status xloader
# Devrait afficher: RUNNING   pid XXXX, uptime X:XX:XX
```

### Vérifier que les logs sont bien configurés
```bash
# Vérifier la config supervisor
cat /etc/supervisord.d/xloader.conf | grep -E "logfile|logger"
```

### Tester manuellement le worker
```bash
# Lancer le worker manuellement pour voir les logs
CKAN_INI=/srv/app/ckan.ini python3 /srv/app/rq_worker_wrapper.py default
```

## 6. Commandes rapides

```bash
# Voir les 50 dernières lignes
supervisorctl tail -50 xloader

# Surveiller en temps réel (filtre xloader)
supervisorctl tail -f xloader | grep -E "\[xloader\]|ERROR|Exception"

# Voir les erreurs récentes
supervisorctl tail -200 xloader | grep -E "ERROR|Exception|Traceback" | tail -20

# Vérifier le statut
supervisorctl status xloader
```



