#!/bin/bash

# Définir CKAN_INI en premier (avant toute utilisation)
CKAN_INI="${CKAN_INI:-/srv/app/ckan.ini}"

# Charger les variables d'environnement du .env si le fichier existe
if [ -f "/srv/app/.env" ]; then
    echo "Chargement des variables depuis .env..."
    export $(grep -v '^#' /srv/app/.env | xargs)
    # Redéfinir CKAN_INI après chargement du .env (au cas où il serait défini dans .env)
    CKAN_INI="${CKAN_INI:-/srv/app/ckan.ini}"
fi

# Afficher les variables importantes pour debug
echo "Variables d'environnement importantes:"
echo "SECRET_KEY: ${SECRET_KEY:-'NON DÉFINIE'}"
echo "CKAN_SITE_URL: ${CKAN_SITE_URL:-'NON DÉFINIE'}"
echo "KEYCLOAK_ISSUER_URL: ${KEYCLOAK_ISSUER_URL:-'NON DÉFINIE'}"
echo "CKAN_INI: ${CKAN_INI}"

if [[ $CKAN__PLUGINS == *"datapusher"* ]]; then
    # Add ckan.datapusher.api_token to the CKAN config file (updated with corrected value later)
    echo "Setting a temporary value for ckan.datapusher.api_token"
    ckan config-tool "$CKAN_INI" ckan.datapusher.api_token=xxx
fi

# Set up the Secret key used by Beaker and Flask
# This can be overriden using a CKAN___BEAKER__SESSION__SECRET env var
if grep -qE "SECRET_KEY ?= ?$" "$CKAN_INI"
then
    echo "Setting SECRET_KEY in ini file"
    ckan config-tool "$CKAN_INI" "SECRET_KEY=$(python3 -c 'import secrets; print(secrets.token_urlsafe())')"
    ckan config-tool "$CKAN_INI" "WTF_CSRF_SECRET_KEY=$(python3 -c 'import secrets; print(secrets.token_urlsafe())')"
    JWT_SECRET=$(python3 -c 'import secrets; print("string:" + secrets.token_urlsafe())')
    ckan config-tool "$CKAN_INI" "api_token.jwt.encode.secret=${JWT_SECRET}"
    ckan config-tool "$CKAN_INI" "api_token.jwt.decode.secret=${JWT_SECRET}"
fi

# Profil DCAT Dataizen : validité des jeux (dct:valid). L'INI fait foi (les variables
# d'environnement dcat ne sont pas appliquées) : on ajoute le plugin dataizen_dcat et
# on déclare le profil dataizen_valid en plus du profil de base euro_dcat_ap_3.
CUR_PLUGINS=$(grep -E '^ckan.plugins' "$CKAN_INI" | head -1 | cut -d= -f2- | sed 's/^ *//')
if ! echo " $CUR_PLUGINS " | grep -q ' dataizen_dcat '; then
    ckan config-tool "$CKAN_INI" "ckan.plugins = $CUR_PLUGINS dataizen_dcat"
fi
ckan config-tool "$CKAN_INI" "ckanext.dcat.rdf.profiles = euro_dcat_ap_3 dataizen_valid"

# CORS lecture seule : autorise les navigateurs d'autres origines (portails des
# instances) à lire les ressources publiques, notamment les tuiles PMTiles du
# relief servies en Range. Données ouvertes, sans credentials : allow-origin *.
ckan config-tool "$CKAN_INI" "ckan.cors.origin_allow_all = true"

# Check if database is already initialized
echo "Vérification si la base est déjà initialisée..."
DB_INITIALIZED=false
if python3 -c "
import psycopg2
import os
try:
    conn_str = os.environ.get('CKAN_SQLALCHEMY_URL', '')
    if conn_str:
        conn = psycopg2.connect(conn_str)
        cursor = conn.cursor()
        cursor.execute(\"\"\"
            SELECT COUNT(*) 
            FROM information_schema.tables 
            WHERE table_schema = 'public';
        \"\"\")
        table_count = cursor.fetchone()[0]
        conn.close()
        if table_count > 0:
            print(f'Base déjà initialisée ({table_count} tables)')
            exit(0)
        else:
            print(' Base vide, initialisation nécessaire')
            exit(1)
except Exception as e:
    print(f' Erreur lors de la vérification: {e}')
    exit(1)
" 2>/dev/null; then
    DB_INITIALIZED=true
fi

# Run the prerun script ONLY if database is not initialized
if [ "$DB_INITIALIZED" = false ]; then
    echo " Initialisation de la base avec prerun.py..."
    python3 prerun.py || echo " prerun.py failed"
else
    echo "Base déjà initialisée, skip de prerun.py"
fi

# Run any startup scripts provided by images extending this one
if [[ -d "/docker-entrypoint.d" ]]
then
    for f in /docker-entrypoint.d/*; do
        case "$f" in
            *.sh)     echo "$0: Running init file $f"; bash "$f" || true ;;
            *.py)     echo "$0: Running init file $f"; python3 "$f" || true ;;
            *)        echo "$0: Ignoring $f (not an sh or py file)" ;;
        esac
    done
fi

# Define default UWSGI options without log redirection to avoid seek issues
# Added --py-autoreload=0 and --buffer-size=65536 to reduce write errors
# Set PYTHONUNBUFFERED=1 to disable Python buffering for stdout/stderr
export PYTHONUNBUFFERED=1
DEFAULT_UWSGI_OPTS="--wsgi-file /srv/app/wsgi.py \
                    --module wsgi:application \
                    --http [::]:5000 \
                    --http-timeout 600 \
                    --master --enable-threads \
                    --threads 3 \
                    --lazy-apps \
                    -p 5 -L -b 32768 --vacuum \
                    --listen 4096 \
                    --harakiri ${UWSGI_HARAKIRI:-300} \
                    --disable-logging \
                    --log-4xx \
                    --log-5xx \
                    --ignore-write-errors \
                    --ignore-sigpipe \
                    --py-autoreload=0 \
                    --buffer-size=131072 \
                    --no-orphans"

# Use UWSGI_OPTS from environment if set, otherwise use defaults
UWSGI_OPTS="${UWSGI_OPTS:-$DEFAULT_UWSGI_OPTS}"

# Append EXTRA_UWSGI_OPTS if set
if [ -n "$EXTRA_UWSGI_OPTS" ]
then
  UWSGI_OPTS="$UWSGI_OPTS $EXTRA_UWSGI_OPTS"
fi

# Attendre que Redis soit disponible avant de démarrer les workers
echo "Attente de Redis pour les workers..."
MAX_WAIT=60
WAITED=0
while [ $WAITED -lt $MAX_WAIT ]; do
    if timeout 2 bash -c "cat < /dev/null > /dev/tcp/redis/6379" 2>/dev/null; then
        echo "Redis est disponible"
        break
    fi
    echo "Attente de Redis... (${WAITED}s/${MAX_WAIT}s)"
    sleep 2
    WAITED=$((WAITED + 2))
done

if [ $WAITED -ge $MAX_WAIT ]; then
    echo "Timeout: Redis n'est pas disponible après ${MAX_WAIT}s, mais on continue quand même"
fi

# NOTE: Le worker jobs (RQ) est géré par supervisor via supervisor.d/xloader.conf
# Ne PAS lancer le worker ici pour éviter les doublons
# Supervisor lance le worker avec le wrapper xloader_wrapper.py qui utilise os.execv
# pour que le worker reste actif en continu (sans --burst)
echo "Worker jobs (RQ) géré par supervisor (supervisor.d/xloader.conf)"

# Start uwsgi without log redirection to avoid seek issues
# Utiliser exec pour que uwsgi devienne le processus principal (PID 1)
echo "Démarrage de uWSGI..."
exec uwsgi $UWSGI_OPTS
