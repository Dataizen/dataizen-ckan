# Identifier les requêtes lentes dans CKAN

## Objectif

Identifier et analyser les requêtes HTTP lentes (>2 secondes) qui saturent les workers uWSGI et provoquent des timeouts.

---

## Méthodes d'identification

### 1. **Analyse des logs CKAN (méthode principale)**

#### A. Utiliser le script de monitoring existant

```bash
# Analyser les dernières 2 heures
docker compose logs --since 2h ckan | python3 /srv/app/scripts/monitor-performance.py --stdin

# Analyser les dernières 10000 lignes
docker compose logs --tail=10000 ckan | python3 /srv/app/scripts/monitor-performance.py --stdin

# Exporter en JSON pour analyse détaillée
docker compose logs --since 1h ckan | python3 /srv/app/scripts/monitor-performance.py --stdin --json /tmp/slow-requests.json
```

**Ce que ça identifie :**
- Requêtes HTTP lentes (>2s) avec URL et durée
- Requêtes DB lentes (>1s)
- OSError "write error"
- Erreurs générales

#### B. Analyser manuellement les logs

```bash
# Filtrer les requêtes lentes (>5 secondes)
docker compose logs ckan | grep "render time" | awk '$NF > 5 {print}'

# Trier par durée (plus lentes en premier)
docker compose logs ckan | grep "render time" | sort -k5 -rn | head -20

# Compter les requêtes lentes par endpoint
docker compose logs --since 1h ckan | grep "render time" | awk '$NF > 2 {print $3}' | sort | uniq -c | sort -rn
```

**Format des logs :**
```
2026-01-26 15:31:20,147 INFO  [ckan.config.middleware.flask_app]  200 /dataset/ render time 100.576 seconds
```

---

### 2. **Activer le logging SQLAlchemy (requêtes DB)**

#### A. Activer dans `ckan.ini`

```ini
[logger_sqlalchemy.engine]
level = INFO
handlers = console

# Pour voir les requêtes SQL complètes
[logger_sqlalchemy.engine.STATEMENT]
level = DEBUG
handlers = console
```

#### B. Analyser les requêtes DB lentes

```bash
# Filtrer les requêtes SQL lentes
docker compose logs ckan | grep -E "SELECT|INSERT|UPDATE|DELETE" | grep -E "\d+\.\d+s"

# Compter les requêtes par table
docker compose logs ckan | grep "SELECT.*FROM" | sed 's/.*FROM \([a-z_]*\).*/\1/' | sort | uniq -c | sort -rn
```

---

### 3. **Utiliser PostgreSQL pour identifier les requêtes lentes**

#### A. Activer `log_min_duration_statement` dans PostgreSQL

```sql
-- Dans le conteneur PostgreSQL
ALTER SYSTEM SET log_min_duration_statement = 1000; -- Log les requêtes > 1 seconde
SELECT pg_reload_conf();
```

#### B. Analyser les logs PostgreSQL

```bash
# Voir les requêtes lentes dans les logs PostgreSQL
docker compose logs db | grep "duration:" | awk -F'duration: ' '{print $2}' | sort -rn | head -20

# Voir les requêtes les plus fréquentes
docker compose logs db | grep "SELECT" | sed 's/.*SELECT/SELECT/' | cut -d' ' -f1-10 | sort | uniq -c | sort -rn | head -20
```

#### C. Utiliser `pg_stat_statements` (extension PostgreSQL)

```sql
-- Activer l'extension
CREATE EXTENSION IF NOT EXISTS pg_stat_statements;

-- Voir les requêtes les plus lentes
SELECT 
    query,
    calls,
    total_exec_time,
    mean_exec_time,
    max_exec_time
FROM pg_stat_statements
ORDER BY mean_exec_time DESC
LIMIT 20;

-- Voir les requêtes les plus fréquentes
SELECT 
    query,
    calls,
    total_exec_time,
    mean_exec_time
FROM pg_stat_statements
ORDER BY calls DESC
LIMIT 20;
```

---

### 4. **Utiliser les outils de profiling Python**

#### A. Activer le middleware de profiling Flask

Ajouter dans le code CKAN (temporairement pour debug) :

```python
# Dans ckanext-admin-tools ou un plugin de debug
from werkzeug.middleware.profiler import ProfilerMiddleware

if config.get('debug', False):
    app.wsgi_app = ProfilerMiddleware(app.wsgi_app, restrictions=[30])
```

#### B. Utiliser `cProfile` pour une requête spécifique

```python
import cProfile
import pstats

profiler = cProfile.Profile()
profiler.enable()

# Exécuter la requête lente
# ...

profiler.disable()
stats = pstats.Stats(profiler)
stats.sort_stats('cumulative')
stats.print_stats(20)  # Top 20 fonctions les plus lentes
```

---

### 5. **Monitoring en temps réel**

#### A. Surveiller les requêtes lentes en direct

```bash
# Filtrer les requêtes > 5 secondes en temps réel
docker compose logs -f ckan | grep "render time" | awk '$NF > 5 {print}'

# Surveiller les erreurs et timeouts
docker compose logs -f ckan | grep -E "timeout|Timeout|OSError|write error"
```

#### B. Utiliser `htop` ou `top` pour voir la charge CPU

```bash
# Dans le conteneur CKAN
docker compose exec ckan htop

# Voir les processus uWSGI et leur utilisation CPU
docker compose exec ckan ps aux | grep uwsgi
```

---

### 6. **Analyser les endpoints spécifiques**

#### A. Identifier les endpoints les plus lents

```bash
# Extraire les endpoints et leurs durées
docker compose logs --since 1h ckan | grep "render time" | \
  awk '{print $3, $NF}' | \
  awk '{sum[$1]+=$2; count[$1]++} END {for (i in sum) print i, sum[i]/count[i], count[i]}' | \
  sort -k2 -rn | head -20
```

#### B. Analyser un endpoint spécifique (ex: `/dataset/`)

```bash
# Voir toutes les requêtes vers /dataset/
docker compose logs --since 1h ckan | grep "/dataset/" | grep "render time"

# Calculer la durée moyenne
docker compose logs --since 1h ckan | grep "/dataset/" | grep "render time" | \
  awk '{sum+=$NF; count++} END {print "Moyenne:", sum/count, "secondes"}'
```

---

## Analyse des résultats

### Requêtes lentes typiques dans CKAN

1. **`/dataset/` (liste des datasets)**
   - **Cause probable** : Requête Solr lente, trop de datasets, pas de pagination
   - **Solution** : Optimiser la requête Solr, ajouter de la pagination, activer le cache

2. **`/dataset/{name}` (page dataset)**
   - **Cause probable** : Requêtes DB multiples (N+1 queries), ressources nombreuses
   - **Solution** : Utiliser `joinedload` dans SQLAlchemy, optimiser les requêtes

3. **`/api/action/datastore_search`**
   - **Cause probable** : Table volumineuse, pas d'index, requête complexe
   - **Solution** : Ajouter des index, limiter les résultats, optimiser la requête

4. **`/api/action/package_search`**
   - **Cause probable** : Requête Solr complexe, trop de résultats
   - **Solution** : Limiter les résultats, optimiser la requête Solr

---

## Outils supplémentaires

### 1. **New Relic / Datadog / APM**

Si vous avez un outil APM (Application Performance Monitoring), utilisez-le pour :
- Identifier automatiquement les requêtes lentes
- Voir les traces complètes (DB, cache, API externes)
- Alertes automatiques

### 2. **Grafana + Prometheus**

Si vous avez un monitoring avec Prometheus :
- Exporter les métriques uWSGI
- Créer des dashboards pour visualiser les temps de réponse
- Alertes sur les requêtes lentes

### 3. **Browser DevTools**

Pour les requêtes côté client :
- Ouvrir DevTools → Network
- Filtrer les requêtes lentes (>2s)
- Voir les détails (timing, waterfall)

---

## Checklist de diagnostic

### Étape 1 : Identifier les requêtes lentes
- [ ] Analyser les logs avec `monitor-performance.py`
- [ ] Identifier les 10 endpoints les plus lents
- [ ] Noter les durées moyennes et maximales

### Étape 2 : Analyser les causes
- [ ] Vérifier les requêtes DB lentes (PostgreSQL logs)
- [ ] Vérifier les requêtes Solr lentes
- [ ] Vérifier les appels API externes
- [ ] Vérifier les calculs lourds dans le code

### Étape 3 : Optimiser
- [ ] Ajouter des index DB manquants
- [ ] Optimiser les requêtes SQL (EXPLAIN ANALYZE)
- [ ] Activer le cache Redis
- [ ] Réduire le nombre de requêtes (N+1 queries)
- [ ] Paginer les résultats volumineux

### Étape 4 : Vérifier l'amélioration
- [ ] Re-analyser les logs après optimisation
- [ ] Comparer les durées avant/après
- [ ] Vérifier que les timeouts ont diminué

---

## Alertes recommandées

Mettre en place des alertes pour :
- Requêtes > 10 secondes
- Plus de 5 requêtes lentes par minute
- Queue uWSGI > 80% de capacité
- CPU > 80% pendant plus de 5 minutes

---

## Ressources

- Script de monitoring : `/srv/app/scripts/monitor-performance.py`
- Documentation monitoring : `/srv/app/scripts/README-MONITORING.md`
- PostgreSQL slow queries : https://www.postgresql.org/docs/current/runtime-config-logging.html
