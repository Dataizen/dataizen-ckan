#!/bin/bash
# Attend que la base datastore soit RÉELLEMENT connectable (pas seulement le port
# postgres ouvert) avant que le worker xloader ne s'initialise. Sinon le plugin
# datastore ne s'enregistre pas dans le contexte du worker et toutes les ingestions
# échouent avec « Action 'datastore_search' not found » (panne récurrente 08/26).
set +e
# 1. port postgres ouvert
until timeout 2 bash -lc 'cat < /dev/null > /dev/tcp/db/5432' 2>/dev/null; do
  echo '[xloader] attente port postgres...'; sleep 2
done
# 2. base datastore connectable avec les identifiants configurés (write_url)
until python3 - <<'PY' 2>/dev/null
import configparser, psycopg2
c = configparser.ConfigParser()
c.read('/srv/app/ckan.ini')
psycopg2.connect(c['app:main']['ckan.datastore.write_url']).close()
PY
do
  echo '[xloader] attente base datastore (connexion reelle)...'; sleep 2
done
echo '[XLOADER] Datastore DB joignable, demarrage du worker'
