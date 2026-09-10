# Moissonnage récurrent : pourquoi ça ne se lançait pas et corrections

## Problème

Les moissonnages planifiés depuis l’interface CKAN (fréquence récurrente) ne se lançaient pas, alors que cron est installé dans l’image.

## Chaîne de fonctionnement

1. **Interface CKAN** : vous configurez une source avec une fréquence (ex. « Tous les jours »). CKAN met à jour `harvest_source.next_run` en base.
2. **Quelque chose doit exécuter** `ckan harvester run` régulièrement. Cette commande :
   - sélectionne les sources actives dont `next_run <= NOW()`;
   - crée des jobs de moissonnage et les envoie vers Redis.
3. **Consumers** (déjà gérés par supervisor) : `harvester-gather` et `harvester-fetch` traitent les jobs dans Redis.

Sans étape 2, aucun job n’est créé, donc aucun moissonnage récurrent.

## Causes identifiées

### 1. BusyBox crond ne lit pas `/etc/cron.d`

- Dans le conteneur, **BusyBox crond** est souvent utilisé (plus simple que le cron Debian, pas de `seteuid`).
- BusyBox crond **ne lit que** `/var/spool/cron/crontabs/<user>`.
- Il **ne lit pas** `/etc/cron.d/`.
- Le fichier `/etc/cron.d/harvest-run` créé par le script n’était donc **jamais exécuté** quand c’est BusyBox crond qui tourne.

### 2. Cron Debian peut ne pas démarrer (USER ckan)

- L’image finale tourne avec **USER ckan** (non-root).
- Le cron Debian a besoin de `seteuid` pour exécuter les tâches sous d’autres utilisateurs.
- En conteneur, sans capabilité particulière, cron peut échouer au démarrage ; le script fait alors `sleep infinity` et **aucune tâche cron ne s’exécute**.

### 3. Utilisateur « root » dans la ligne cron

- La ligne cron utilisait `root` comme utilisateur d’exécution.
- Avec BusyBox crond lancé en tant que `ckan`, exécuter la commande en tant que `root` nécessiterait un `setuid` qui peut échouer ou être ignoré.

## Corrections appliquées

### 1. Script `36-setup-harvest-cron.sh`

- **`/etc/cron.d/harvest-run`** : la ligne cron utilise maintenant l’utilisateur **`ckan`** au lieu de `root` (pour le cas où le cron Debian est utilisé).
- **Crontab BusyBox** : création d’un vrai crontab pour l’utilisateur `ckan` dans **`/var/spool/cron/crontabs/ckan`** avec la même commande (sans champ « user »). Ainsi, si c’est BusyBox crond qui tourne, la tâche est bien lue et exécutée.

### 2. Programme supervisor `harvest-scheduler`

- Un nouveau programme supervisor **`harvest-scheduler`** a été ajouté dans `supervisor.d/harvester.conf`.
- Il exécute en boucle :
  - `ckan harvester run`
  - puis `sleep 3600` (1 heure).
- **Avantages** :
  - Ne dépend pas de cron.
  - Toujours actif tant que le conteneur et supervisor tournent.
  - Logs visibles dans la sortie du conteneur.

Les moissonnages récurrents peuvent donc fonctionner soit via cron (si cron tourne correctement), soit via `harvest-scheduler`.

## Vérifications utiles

1. **Logs du planificateur** (si vous utilisez `harvest-scheduler`) :
   ```bash
   docker compose logs -f ckan 2>&1 | grep harvest-scheduler
   ```

2. **Logs cron** (si vous utilisez le cron) :
   ```bash
   docker compose exec ckan cat /var/log/harvest-run.log
   ```

3. **Lancer à la main** (test) :
   ```bash
   docker compose exec ckan ckan -c /srv/app/ckan.ini harvester run
   ```

4. **Vérifier que les sources ont une date `next_run`** (en base ou via l’interface) et que les sources sont **actives**.

## Résumé

| Problème                         | Correction                                              |
|----------------------------------|---------------------------------------------------------|
| BusyBox crond ne lit pas cron.d  | Crontab dans `/var/spool/cron/crontabs/ckan`            |
| Cron Debian peut ne pas démarrer | Utilisateur `ckan` dans `/etc/cron.d/harvest-run`       |
| Pas de solution de secours       | Programme supervisor `harvest-scheduler` (boucle 1 h)  |

Après rebuild/redeploy, les moissonnages récurrents devraient se lancer soit par cron, soit par `harvest-scheduler`.
