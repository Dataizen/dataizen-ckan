FROM postgres:15
LABEL Open Knowledge Foundation

# Install PostGIS (base installation for arm64 compatibility)
RUN apt-get update && \
    apt-get install -y \
        postgresql-15-postgis-3 \
        postgresql-15-postgis-3-scripts && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/* && \
    rm -rf /var/cache/apt/*

# Allow connections; we don't map out any ports so only linked docker containers can connect
RUN echo "host all  all    0.0.0.0/0  md5" >> /var/lib/postgresql/data/pg_hba.conf

# Customize default user/pass/db
ENV POSTGRES_DB ckan
ENV POSTGRES_USER ckan
ARG POSTGRES_PASSWORD
ARG DATASTORE_READONLY_PASSWORD

# Include extra setup scripts (eg datastore)
ADD docker-entrypoint-initdb.d /docker-entrypoint-initdb.d

# Script pour appliquer les timeouts à chaque démarrage
RUN echo '#!/bin/bash\n\
set -e\n\
# Attendre que PostgreSQL soit prêt\n\
until pg_isready -U "$POSTGRES_USER" > /dev/null 2>&1; do sleep 1; done\n\
# Appliquer les timeouts si pas déjà configurés\n\
psql -v ON_ERROR_STOP=0 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-EOSQL || true\n\
    ALTER SYSTEM SET idle_in_transaction_session_timeout = '\''300000'\'';\n\
    ALTER SYSTEM SET idle_session_timeout = '\''600000'\'';\n\
    SELECT pg_reload_conf();\n\
EOSQL\n\
' > /docker-entrypoint-initdb.d/99_apply_timeouts.sh && \
    chmod +x /docker-entrypoint-initdb.d/99_apply_timeouts.sh

# Créer un script de démarrage personnalisé qui s'exécute après l'initialisation
RUN echo '#!/bin/bash\n\
set -e\n\
# Exécuter le script d'\''entrée PostgreSQL par défaut\n\
exec /usr/local/bin/docker-entrypoint.sh "$@"\n\
' > /usr/local/bin/custom-entrypoint.sh && \
    chmod +x /usr/local/bin/custom-entrypoint.sh