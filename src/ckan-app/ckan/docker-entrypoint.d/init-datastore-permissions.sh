#!/bin/bash
echo "Initialisation des permissions du DataStore..."

# set-permissions doit s'executer en SUPERUSER postgres (cf. l'en-tete du SQL genere).
# En dev local (docker-compose), le role datapusher/datapusher@db convient.
# En PROD, ce role n'est pas superuser et n'a pas ce mot de passe : l'etape est alors
# geree par Ansible (role svc_shared) via le superuser dataizen. On echoue donc
# silencieusement ici au lieu de logguer une erreur d'auth trompeuse.
if ckan -c /srv/app/ckan.ini datastore set-permissions 2>/dev/null \
     | psql postgresql://datapusher:datapusher@db/postgres >/dev/null 2>&1; then
  echo "Permissions du DataStore appliquées (contexte dev local)."
else
  echo "set-permissions non applique ici (normal en prod : gere par Ansible en superuser)."
fi

echo "re-indexing datastore"
ckan -c /srv/app/ckan.ini search-index rebuild

