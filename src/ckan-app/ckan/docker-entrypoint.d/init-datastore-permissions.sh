#!/bin/bash
echo "Initialisation des permissions du DataStore..."

ckan -c /srv/app/ckan.ini datastore set-permissions | psql postgresql://datapusher:iTAcdBEcee6GQmJM@db/postgres

if [ $? -eq 0 ]; then
  echo "Permissions du DataStore appliquées avec succès."
else
  echo "Échec lors de l'application des permissions du DataStore."
fi

echo "re-indexing datastore"
ckan -c /srv/app/ckan.ini search-index rebuild

