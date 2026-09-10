# Explication : `ckan jobs worker` vs Worker RQ

## Clarification importante

### `ckan jobs worker` (commande CKAN)

C'est une **commande CLI CKAN** qui :
1. Initialise l'environnement CKAN (charge `ckan.ini`, configure SQLAlchemy, etc.)
2. Lance un **worker RQ** en interne pour traiter les jobs

**Commande actuelle :**
```bash
ckan -c /srv/app/ckan.ini jobs worker default
```

### Worker RQ (processus interne)

Le **worker RQ** est le processus Python qui :
1. Se connecte à Redis
2. Écoute les queues (ex: `ckan:default:default`)
3. Récupère les jobs depuis Redis
4. **Exécute les fonctions Python** (ex: `xloader_data_into_datastore`)

## Le problème

Même si `ckan jobs worker` initialise CKAN dans le processus parent, **le worker RQ qui exécute les jobs** peut ne pas avoir accès à cet environnement initialisé.

### Pourquoi ?

RQ peut exécuter les jobs de différentes façons :

1. **Dans le même processus** (thread) → L'environnement CKAN devrait être disponible 
2. **Dans un processus séparé** (fork/subprocess) → L'environnement CKAN n'est **pas** propagé 
3. **Dans un contexte isolé** → L'environnement CKAN n'est **pas** disponible 

## Architecture actuelle

```
Supervisor
  └─> ckan jobs worker default
        ├─> Initialise CKAN (load_config, load_environment)
        │     └─> Configure model.Session.bind
        │
        └─> Lance Worker RQ
              └─> Écoute Redis queue: ckan:default:default
                    └─> Exécute xloader_data_into_datastore()
                          └─> model.Session.bind est None !
```

## Le problème identifié

D'après le diagnostic :
- `ckan jobs worker` est bien utilisé 
- Mais `model.Session.bind` est `None` dans le contexte qui exécute les jobs 

**Conclusion :** L'initialisation CKAN faite par `ckan jobs worker` n'est **pas propagée** au contexte qui exécute les jobs RQ.

## Solutions possibles

### Solution 1 : Utiliser le wrapper `rq_worker_wrapper.py`

Le wrapper initialise explicitement CKAN **avant** de lancer le worker RQ :

```python
# rq_worker_wrapper.py
def initialize_ckan():
    from ckan.config.environment import load_config, load_environment
    config = load_config('/srv/app/ckan.ini')
    load_environment(config)  # Configure model.Session.bind

# Puis lance le worker RQ
worker = Worker([queue])
worker.work()  # Les jobs exécutés auront accès à l'environnement CKAN initialisé
```

**Avantage :** Garantit que CKAN est initialisé dans le même processus que le worker RQ.

**Inconvénient :** Nécessite de passer le nom de queue complet (`ckan:default:default`) au lieu de `default`.

### Solution 2 : Forcer l'initialisation dans chaque job

Modifier `xloader_data_into_datastore` pour initialiser CKAN au début si nécessaire :

```python
def xloader_data_into_datastore(input):
    from ckan import model
    if model.Session.bind is None:
        from ckan.config.environment import load_config, load_environment
        config = load_config('/srv/app/ckan.ini')
        load_environment(config)
    # ... reste du code
```

**Avantage :** Fonctionne même si l'initialisation n'est pas faite au niveau du worker.

**Inconvénient :** Overhead à chaque job (mais négligeable si l'initialisation est déjà faite).

### Solution 3 : Vérifier pourquoi `ckan jobs worker` n'initialise pas correctement

Il faut examiner le code source de `ckan/cli/jobs.py` pour comprendre :
- Comment `ckan jobs worker` initialise CKAN
- Pourquoi l'initialisation n'est pas propagée aux jobs
- Si c'est un bug ou un comportement attendu

## Recommandation

**Utiliser le wrapper `rq_worker_wrapper.py`** car :
1. Il garantit l'initialisation CKAN dans le même processus que le worker RQ
2. Il est plus simple et plus fiable que de modifier chaque job
3. Il permet de contrôler exactement quand et comment CKAN est initialisé

## Modification nécessaire

Modifier `supervisor.d/xloader.conf` :

```ini
# Avant
command=/bin/bash -lc '... exec ckan -c /srv/app/ckan.ini jobs worker default'

# Après
command=/bin/bash -lc '... exec python3 /srv/app/rq_worker_wrapper.py default'
```

**Note :** Le wrapper doit être modifié pour utiliser `default` (CKAN préfixera automatiquement) ou `ckan:default:default` selon comment CKAN gère le préfixage.





