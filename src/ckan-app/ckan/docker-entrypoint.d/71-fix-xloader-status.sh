#!/bin/bash
# Fiabilise le statut d'ingestion (xloader) au démarrage de CKAN. Idempotent, non bloquant.
#
# 1. Supprime les statuts xloader ORPHELINS (task_status pointant vers une ressource qui
#    n'existe plus, après purge d'un jeu) : ils gonflaient à tort le compteur d'« erreurs ».
# 2. Marque `complete` les CSV DÉRIVÉS (sorties harmonisées, converted_from_json.csv…)
#    déjà chargés au datastore (datastore_active=true, xloader_skip=true) mais SANS statut :
#    ils s'affichaient sans badge alors que la donnée est bien là. On leur pose le badge
#    « données chargées » attendu.
#
# Se rejoue à chaque démarrage : auto-réparation, sans dépendre d'un reset manuel.
set -uo pipefail
echo "== 71-fix-xloader-status : fiabilisation du statut d'ingestion"

python3 << 'EOF' || echo "== 71-fix-xloader-status : ignoré (non bloquant)"
import os, json, uuid
try:
    import psycopg2
except Exception as e:
    print(f"[fix-xloader-status] psycopg2 indisponible: {e}"); raise SystemExit(0)

conn_str = os.environ.get('CKAN_SQLALCHEMY_URL', '')
if not conn_str:
    print('[fix-xloader-status] CKAN_SQLALCHEMY_URL non défini'); raise SystemExit(0)

try:
    conn = psycopg2.connect(conn_str)
    conn.autocommit = True
    cur = conn.cursor()

    # 1. statuts xloader orphelins (ressource supprimée/purgée)
    cur.execute("""DELETE FROM task_status
                   WHERE task_type='xloader'
                     AND entity_id NOT IN (SELECT id FROM resource)""")
    orphans = cur.rowcount

    # 2. backfill des CSV dérivés chargés au datastore, sans statut -> complete
    cur.execute("""SELECT r.id, r.extras
                   FROM resource r
                   LEFT JOIN task_status ts
                     ON ts.entity_id=r.id AND ts.task_type='xloader'
                   WHERE r.state='active' AND r.format='CSV' AND ts.id IS NULL""")
    added = 0
    for rid, extras in cur.fetchall():
        try:
            ex = json.loads(extras) if extras else {}
        except Exception:
            ex = {}
        if str(ex.get('datastore_active')).lower() == 'true' \
           and str(ex.get('xloader_skip')).lower() == 'true':
            cur.execute("""INSERT INTO task_status
                             (id, entity_id, entity_type, task_type, key, value, state, error, last_updated)
                           VALUES (%s, %s, 'resource', 'xloader', 'xloader',
                                   '{"job_id": "derived"}', 'complete', '{}', now())
                           ON CONFLICT (entity_id, task_type, key) DO NOTHING""",
                        (uuid.uuid4().hex, rid))
            added += cur.rowcount
    print(f"[fix-xloader-status] orphelins supprimés={orphans}, CSV dérivés marqués complete={added}")
    cur.close(); conn.close()
except Exception as e:
    print(f"[fix-xloader-status] erreur (ignorée): {e}")
EOF
