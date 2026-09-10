# Monitoring des performances CKAN

## Scripts disponibles

### 1. `monitor-performance.py` - Analyse détaillée des logs

Analyse les logs CKAN pour identifier les problèmes de performance, les OSError, et les requêtes lentes.

**Usage:**
```bash
# Analyser les dernières heures
python3 /srv/app/scripts/monitor-performance.py --hours 2

# Analyser les dernières 10000 lignes
python3 /srv/app/scripts/monitor-performance.py --tail 10000

# Depuis Docker (recommandé)
docker compose logs --tail=10000 ckan | python3 /srv/app/scripts/monitor-performance.py --stdin

# Exporter en JSON
python3 /srv/app/scripts/monitor-performance.py --hours 1 --json /tmp/performance-report.json
```

**Ce qu'il analyse:**
- OSError "write error" (peuvent ralentir l'application)
- Requêtes HTTP lentes (>2s)
- Requêtes DB lentes (>1s)
- Erreurs générales
- Recommandations d'optimisation

### 2. `check-system-performance.sh` - Vérification rapide système

Vérification rapide de l'état du système (CPU, mémoire, disque, DB).

**Usage:**
```bash
# Dans le conteneur CKAN
/srv/app/scripts/check-system-performance.sh

# Depuis l'hôte
docker compose exec ckan /srv/app/scripts/check-system-performance.sh
```

### 3. `monitor-realtime.sh` - Monitoring en temps réel

Surveille les OSError et erreurs en temps réel.

**Usage:**
```bash
# Dans le conteneur CKAN
/srv/app/scripts/monitor-realtime.sh

# Depuis l'hôte
docker compose exec ckan /srv/app/scripts/monitor-realtime.sh

# Avec Docker logs (recommandé)
docker compose logs -f ckan | grep -i "oserror\|write error\|ERROR"
```

## OSError "write error" - Explication

Les OSError "write error" se produisent quand:
- **stdout/stderr est saturé** (trop de logs simultanés)
- **Buffer système plein** (système surchargé)
- **Problème de permissions** (rare)

**Impact sur les performances:**
- Chaque OSError bloque brièvement le thread qui écrit le log
- Si fréquents (>50/heure), cela peut ralentir l'application
- Les handlers de log tentent de réessayer, consommant du CPU

**Solutions:**
1. **Réduire le niveau de log** dans `ckan.ini`:
   ```ini
   # Changer de DEBUG à INFO
   [logger_root]
   level = INFO
   ```

2. **Utiliser le SafeStreamHandler** (déjà activé dans le plugin OGC)

3. **Limiter les logs verbeux**:
   - Désactiver les logs DEBUG pour les requêtes fréquentes
   - Utiliser un handler de log asynchrone

4. **Vérifier l'espace disque**:
   ```bash
   df -h
   ```

5. **Augmenter les buffers système** (si root):
   ```bash
   # Augmenter la taille des buffers
   sysctl -w kernel.printk_ratelimit=10
   ```

## Workflow recommandé

### Diagnostic initial
```bash
# 1. Vérification système rapide
docker compose exec ckan /srv/app/scripts/check-system-performance.sh

# 2. Analyse des logs (dernières 2 heures)
docker compose logs --since 2h ckan | python3 /srv/app/scripts/monitor-performance.py --stdin

# 3. Monitoring en temps réel (5 minutes)
timeout 300 docker compose logs -f ckan | grep -i "oserror\|write error" | head -20
```

### Si beaucoup d'OSError détectés
```bash
# 1. Vérifier l'espace disque
docker compose exec ckan df -h

# 2. Compter les OSError par heure
docker compose logs --since 1h ckan | grep -ci "oserror\|write error"

# 3. Vérifier le niveau de log
docker compose exec ckan grep -i "level" /srv/app/ckan.ini | grep -i "log"

# 4. Réduire le niveau de log si nécessaire
# Éditer ckan.ini et changer DEBUG -> INFO
```

### Monitoring continu
```bash
# Créer un cron job pour surveiller quotidiennement
# Dans le conteneur CKAN:
0 9 * * * /srv/app/scripts/monitor-performance.py --hours 24 --json /tmp/daily-performance-$(date +\%Y\%m\%d).json
```

## Métriques à surveiller

### Bon état
- OSError: < 10/heure
- Requêtes lentes: < 5/heure
- Temps de réponse API: < 500ms
- CPU idle: > 50%
- Mémoire disponible: > 20%

### Attention
- OSError: 10-50/heure
- Requêtes lentes: 5-20/heure
- Temps de réponse API: 500ms-2s
- CPU idle: 30-50%
- Mémoire disponible: 10-20%

### Problème
- OSError: > 50/heure
- Requêtes lentes: > 20/heure
- Temps de réponse API: > 2s
- CPU idle: < 30%
- Mémoire disponible: < 10%

## Exemples de sortie

### `monitor-performance.py`
```
RAPPORT DE PERFORMANCE CKAN
================================================================================

OSError 'write error': 127 occurrences
   ATTENTION: 127 OSError détectés!
   Cela peut significativement ralentir l'application.
   
   Répartition des erreurs:
      - write error: 120
      - Broken pipe: 7

Requêtes lentes (>2s): 8
   Top 5 des endpoints les plus lents:
      - /api/3/action/package_search: 3 requêtes, moyenne 3.45s
      - /dataset/randonnees_de_la_ccmt: 2 requêtes, moyenne 2.12s

RECOMMANDATIONS
================================================================================
1. OSError fréquents:
   - Vérifier l'espace disque: df -h
   - Réduire le niveau de log dans ckan.ini
```

### `check-system-performance.sh`
```
Vérification des performances système CKAN
==============================================

CPU:
   Utilisation:
   - Idle: 45.2%
   Processus CKAN:
   - Nombre de processus: 8

Mémoire:
   - Total: 8.0Gi
   - Utilisé: 5.2Gi (65%)
   - Disponible: 2.1Gi

Disque:
   - Utilisation: 45% (45G/100G)
   - Mapfiles: 12% (1.2G/10G)

OSError récents (dernières 1000 lignes):
   - Nombre: 23
```
