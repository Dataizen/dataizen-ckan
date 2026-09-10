FROM ckan/ckan-base:2.11


ENV APP_DIR=/srv/app
ENV CKAN_INI=${APP_DIR}/ckan.ini
ENV PYTHONWARNINGS=ignore::UserWarning:pkg_resources
ENV MPLCONFIGDIR=/tmp/matplotlib-cache

USER root
ARG TZ
RUN echo $TZ > /etc/timezone
RUN [ "$TZ" != "UTC" ] && cp /usr/share/zoneinfo/$TZ /etc/localtime || echo "Timezone already UTC"

# Install CKAN extensions package which includes envvars
RUN pip install ckan[extensions]


RUN apt-get update && apt-get install -y git && rm -rf /var/lib/apt/lists/*

# Install Python packages and CKAN extensions
RUN pip install --no-cache-dir \
    pika factory_boy mock pytest pandas geopandas geojson pyproj pyyaml owslib openpyxl xlrd && \
    pip install -e git+https://github.com/ckan/ckanext-harvest.git#egg=ckanext-harvest && \
    pip install -e git+https://github.com/ckan/ckanext-dcat.git#egg=ckanext-dcat && \
    pip install -e git+https://github.com/ckan/ckanext-geoview.git#egg=ckanext-geoview && \
    pip install -e git+https://github.com/ckan/ckanext-spatial.git#egg=ckanext-spatial && \
    pip install -e git+https://github.com/ckan/ckanext-scheming.git#egg=ckanext-scheming || echo "⚠️ ckanext-scheming installation failed, continuing..." && \
    pip install -e git+https://github.com/ckan/ckanext-showcase.git#egg=ckanext-showcase || echo "⚠️ ckanext-showcase installation failed, continuing..." && \
    pip install ckanext-composite || echo "⚠️ ckanext-composite installation failed, continuing..."

# Install system libraries needed for Pillow, matplotlib and image processing
# Also install cron for scheduled harvest jobs, postgresql-client and redis-cli
RUN apt-get update && apt-get install -y \
    libjpeg-dev \
    libpng-dev \
    libfreetype6-dev \
    liblcms2-dev \
    libwebp-dev \
    libtiff5-dev \
    libopenjp2-7-dev \
    python3-dev \
    cron \
    busybox \
    postgresql-client \
    redis-tools \
    && rm -rf /var/lib/apt/lists/*

# Install Python image processing libraries (used by OGC providers)
RUN pip install --no-cache-dir \
    Pillow \
    numpy \
    matplotlib \
    folium \
    branca

# Create map rendering directory and matplotlib cache
RUN mkdir -p /srv/app/maps && \
    mkdir -p /tmp/matplotlib-cache && \
    mkdir -p /tmp/fontconfig-cache && \
    chown ckan /srv/app/maps && \
    chown ckan /tmp/matplotlib-cache && \
    chown ckan /tmp/fontconfig-cache



# Install harvest-xloader extension
COPY extensions/ckanext-harvest-xloader /srv/app/ckanext-harvest-xloader
RUN pip install -e /srv/app/ckanext-harvest-xloader

# Install dataload-router extension
COPY extensions/ckanext-dataload-router /srv/app/ckanext-dataload-router
RUN pip install -e /srv/app/ckanext-dataload-router

# Install OGC extension
COPY extensions/ckanext-ogc /srv/app/ckanext-ogc
RUN pip install -e /srv/app/ckanext-ogc

# Install Keycloak extension for Keycloak authentication
RUN pip install python-keycloak
RUN pip install PyJWT  # For JWT token decoding
RUN pip install flask-debugtoolbar

# Copy and install ckanext-keycloak from local source
COPY extensions/ckanext-keycloak /srv/app/ckanext-keycloak
RUN pip install -e /srv/app/ckanext-keycloak

# Install custom-licenses extension for loading licenses from JSON file
COPY extensions/ckanext-custom-licenses /srv/app/ckanext-custom-licenses
RUN pip install -e /srv/app/ckanext-custom-licenses

# Install admin-tools extension for bulk operations and sync management
COPY extensions/ckanext-admin-tools /srv/app/ckanext-admin-tools
RUN pip install -e /srv/app/ckanext-admin-tools

# Install dcat-patch extension to handle missing scheming_datasets
COPY extensions/ckanext-dcat-patch /srv/app/ckanext-dcat-patch
RUN pip install -e /srv/app/ckanext-dcat-patch

# Install dolfin extension (harmonisation semantique DOLFIN, extraite de dataload-router)
COPY extensions/ckanext-dolfin /srv/app/ckanext-dolfin
RUN pip install -e /srv/app/ckanext-dolfin

# Install rag extension (fraicheur de l'index RAG : reindex dtz-rag au depot/maj)
COPY extensions/ckanext-rag /srv/app/ckanext-rag
RUN pip install -e /srv/app/ckanext-rag

# Copy basemaps.json for geoview
COPY config_files/common/basemaps.json /srv/app/config_files/common/basemaps.json
# Copy licenses.json for CKAN licenses configuration
COPY config_files/common/licenses.json /srv/app/config_files/common/licenses.json
RUN mkdir -p /srv/app/config_files/common && chown -R ckan:ckan-sys /srv/app/config_files/

# Configuration des templates personnalisés
RUN ckan config-tool /srv/app/ckan.ini "extra_template_paths = /srv/app/templates"

# Créer le répertoire pour les templates personnalisés
RUN mkdir -p /srv/app/templates/snippets

# Copier les templates personnalisés pour les boutons SSO
COPY templates/snippets/login_form.html /srv/app/templates/snippets/login_form.html
COPY templates/snippets/login_form_simple.html /srv/app/templates/snippets/login_form_simple.html

# Pied de page : lien « Code source » (offre de source AGPL article 13)
COPY templates/footer.html /srv/app/templates/footer.html

# Copier le script de maintenance plugin_extras
COPY fix-plugin-extras.sh /srv/app/fix-plugin-extras.sh
# Copier et configurer le cron job
COPY setup-cron.sh /srv/app/setup-cron.sh
# Copier le script de test des routes SSO
COPY test-sso-routes.sh /srv/app/test-sso-routes.sh
RUN chmod +x /srv/app/fix-plugin-extras.sh /srv/app/setup-cron.sh /srv/app/test-sso-routes.sh && \
    /srv/app/setup-cron.sh

# Ajouter l'exécution du script au démarrage
# TEMPORAIREMENT DÉSACTIVÉ: Keycloak - décommenter la ligne suivante pour réactiver
RUN echo '#!/bin/bash' > /srv/app/start-ckan.sh && \
    echo 'echo "🚀 Démarrage de CKAN..."' >> /srv/app/start-ckan.sh && \
    # echo '/docker-entrypoint.d/60-configure-keycloak.sh' >> /srv/app/start-ckan.sh && \
    echo 'exec "$@"' >> /srv/app/start-ckan.sh && \
    chmod +x /srv/app/start-ckan.sh

# Install xloader extension
RUN pip install -e git+https://github.com/ckan/ckanext-xloader.git@2.1.0#egg=ckanext-xloader

# Install xloader dependencies
RUN pip install "tabulator" "unidecode" "chardet<6" "requests>=2.32.0" "urllib3>=2.2.2" "zipp>=3.19.1" && \
    python -c "from chardet.universaldetector import UniversalDetector; print('chardet ok')"

# Fix RQ version to 1.15.x to avoid intermediate queue issues in RQ 1.16.x
# RQ 1.16.x introduced "intermediate queue" which can cause worker restarts
# See: https://github.com/rq/rq/issues/XXX (intermediate queue issues)
RUN pip install "rq>=1.15.0,<1.16.0"

COPY allowed_functions.txt ${APP_DIR}/allowed_functions.txt

RUN ckan config-tool ${CKAN_INI} "ckan.max_resource_size=1000" && \
    ckan config-tool ${CKAN_INI} "ckan.datastore.search.rows_max=300000"

# Copy harvester configuration and initialization files
COPY docker-entrypoint.d/ /docker-entrypoint.d/

# Copy CSW synchronization script and mapfile sync script
COPY scripts/ /srv/app/scripts/
RUN chmod +x /srv/app/scripts/*.sh /srv/app/scripts/*.py /docker-entrypoint.d/*.sh /docker-entrypoint.d/*.py || true

# TEMPORAIREMENT DÉSACTIVÉ: Exécution du script de configuration Keycloak au build
# Pour réactiver Keycloak, décommenter la ligne suivante:
# RUN /docker-entrypoint.d/60-configure-keycloak.sh

COPY supervisor.d/harvester.conf /etc/supervisord.d/
COPY supervisor.d/xloader.conf /etc/supervisord.d/
COPY supervisor.d/geoworker.conf /etc/supervisord.d/
COPY supervisor.d/cron.conf /etc/supervisord.d/

RUN apt-get update && apt-get install -y sudo procps net-tools curl jq gdal-bin libgdal-dev python3-gdal redis-tools && \
    echo "ckan ALL=(ALL) NOPASSWD: ALL" > /etc/sudoers.d/ckan && \
    chmod 0440 /etc/sudoers.d/ckan && \
    rm -f /etc/supervisor/supervisord.conf && \
    DEBIAN_FRONTEND=noninteractive apt-get install -y supervisor && \
    mkdir -p /var/log/supervisor && \
    chown -R ckan /var/log/supervisor && \
    rm -rf /var/lib/apt/lists/*

# Copy supervisord configuration after supervisor installation
COPY supervisord.conf /etc/supervisor/supervisord.conf
# Install pygeoapi and dependencies (latest version)
RUN pip install --upgrade pip
RUN pip install "pygeoapi>=0.18.0" psycopg2-binary requests flask-cors fiona shapely
# Install pycsw for CSW service (Catalogue Service for the Web)
RUN pip install "pycsw>=2.8.0" lxml owslib || pip install pycsw lxml owslib
RUN pip cache purge
RUN python -c "import pygeoapi; print(f'pygeoapi version: {pygeoapi.__version__}')"
RUN python -c "import pycsw; print(f'pycsw version: {pycsw.__version__}')"

# Apply pygeoapi patches
COPY patches/pygeoapi/api/itemtypes.py /usr/local/lib/python3.10/site-packages/pygeoapi/api/itemtypes.py

# Install CKAN provider for pygeoapi directly (now in OGC extension)
COPY extensions/ckanext-ogc/pygeoapi-providers/ckan_provider /usr/local/lib/python3.10/site-packages/ckan_provider
RUN cd /usr/local/lib/python3.10/site-packages/ckan_provider && python setup.py install

# Copy pygeoapi base configuration (now in OGC extension)
# This is the template that will be used to generate local.config.yml
COPY extensions/ckanext-ogc/pygeoapi/local.config.base.yml /srv/app/pygeoapi/local.config.base.yml
RUN chown -R ckan:ckan-sys /srv/app/pygeoapi/

# Copy OGC scripts to /srv/app/pygeoapi-providers/ckan_provider/ for easy access
RUN mkdir -p /srv/app/pygeoapi-providers/ckan_provider
COPY extensions/ckanext-ogc/pygeoapi-providers/ckan_provider/*.py /srv/app/pygeoapi-providers/ckan_provider/
COPY extensions/ckanext-ogc/pygeoapi-providers/ckan_provider/README.md /srv/app/pygeoapi-providers/ckan_provider/
COPY extensions/ckanext-ogc/pygeoapi-providers/ckan_provider/requirements.txt /srv/app/pygeoapi-providers/ckan_provider/
COPY extensions/ckanext-ogc/pygeoapi-providers/ckan_provider/setup.py /srv/app/pygeoapi-providers/ckan_provider/
RUN chmod +x /srv/app/pygeoapi-providers/ckan_provider/*.py
RUN chown -R ckan /srv/app/pygeoapi-providers/

# Copy default openapi.yml (instead of using echo to avoid Windows CRLF issues)
COPY pygeoapi-openapi.yml /srv/app/pygeoapi/openapi.yml

# Ensure correct permissions for pygeoapi directory and all files
RUN chown -R ckan:ckan-sys /srv/app/pygeoapi/

COPY start-pygeoapi.sh /srv/app/start-pygeoapi.sh
RUN chmod +x /srv/app/start-pygeoapi.sh

# Copy OGC scripts from extension
COPY extensions/ckanext-ogc/scripts/fix-ogc-issues.sh /srv/app/fix-ogc-issues.sh
COPY extensions/ckanext-ogc/scripts/add-test-dataset.sh /srv/app/add-test-dataset.sh
COPY extensions/ckanext-ogc/scripts/restart-pygeoapi.sh /srv/app/restart-pygeoapi.sh
COPY extensions/ckanext-ogc/scripts/start-pygeoapi-port.sh /srv/app/start-pygeoapi-port.sh
RUN chmod +x /srv/app/restart-pygeoapi.sh /srv/app/start-pygeoapi-port.sh

COPY extensions/ckanext-ogc/scripts/check-files.sh /srv/app/check-files.sh
RUN chmod +x  /srv/app/fix-ogc-issues.sh /srv/app/add-test-dataset.sh /srv/app/check-files.sh

# Copy OGC Python scripts to expected locations
# Create directory for OGC scripts and copy scripts
RUN mkdir -p /srv/app/ckanext-ogc/scripts
COPY extensions/ckanext-ogc/scripts/generate-mapfile.py /srv/app/ckanext-ogc/scripts/generate-mapfile.py
COPY extensions/ckanext-ogc/scripts/generate-mapfile.py /usr/local/bin/generate-mapfile.py
COPY scripts/import-geospatial-to-datagis.py /usr/local/bin/import-geospatial-to-datagis.py
COPY scripts/analyze-datagis.py /usr/local/bin/analyze-datagis.py
RUN chmod +x /srv/app/ckanext-ogc/scripts/generate-mapfile.py /usr/local/bin/generate-mapfile.py /usr/local/bin/import-geospatial-to-datagis.py /usr/local/bin/analyze-datagis.py && \
    chown -R ckan:ckan-sys /srv/app/ckanext-ogc/

# Copy Keycloak test script
COPY test-keycloak-config.sh /srv/app/test-keycloak-config.sh

# Copy custom CKAN startup script with logs redirected to stdout/stderr
COPY start_ckan_with_logs.sh /srv/app/start_ckan_with_logs.sh

# Copy custom supervisord configuration with logs redirected to stdout/stderr
COPY supervisord_with_logs.conf /etc/supervisor/supervisord.conf

# Copy supervisor startup wrapper script
COPY start-supervisor.sh /srv/app/start-supervisor.sh

# Create xloader wrapper script for error capture
RUN cat > /srv/app/xloader_wrapper.py << 'PYTHON_WRAPPER' && chmod +x /srv/app/xloader_wrapper.py
#!/usr/bin/env python3
"""
Wrapper pour le worker xloader afin de capturer toutes les erreurs et exceptions.
"""
import sys
import traceback
import logging

# Configurer le logging pour capturer toutes les erreurs
logging.basicConfig(
    level=logging.DEBUG,
    format='[XLOADER] %(asctime)s %(levelname)s %(name)s: %(message)s',
    stream=sys.stderr
)

logger = logging.getLogger('xloader_wrapper')

def excepthook(exc_type, exc_value, exc_traceback):
    """Capture toutes les exceptions non gérées."""
    if issubclass(exc_type, KeyboardInterrupt):
        sys.__excepthook__(exc_type, exc_value, exc_traceback)
        return
    
    logger.error("=" * 80)
    logger.error("XLOADER FATAL ERROR - Uncaught exception:")
    logger.error("Type: %s", exc_type.__name__)
    logger.error("Value: %s", exc_value)
    logger.error("Traceback:")
    for line in traceback.format_exception(exc_type, exc_value, exc_traceback):
        logger.error(line.rstrip())
    logger.error("=" * 80)
    sys.__excepthook__(exc_type, exc_value, exc_traceback)

# Installer le hook d'exception
sys.excepthook = excepthook

# Importer et exécuter la commande CKAN
if __name__ == '__main__':
    try:
        # Utiliser subprocess pour appeler la commande ckan directement
        # Cela évite les problèmes d'import et garantit que la CLI fonctionne correctement
        import subprocess
        
        logger.info("=" * 80)
        logger.info("Starting xloader worker with error capture enabled...")
        logger.info("Arguments: %s", sys.argv[1:])
        
        # Vérifier les permissions et l'accès aux ressources nécessaires
        import os
        import stat
        
        # Vérifier les permissions sur les répertoires de stockage CKAN
        storage_paths = [
            '/var/lib/ckan/storage',
            '/var/lib/ckan/resources',
            '/var/lib/ckan/default'
        ]
        
        logger.info("Checking storage permissions...")
        for path in storage_paths:
            if os.path.exists(path):
                stat_info = os.stat(path)
                logger.info("  %s: mode=%o, uid=%d, gid=%d, readable=%s, writable=%s",
                           path, stat_info.st_mode & 0o777, stat_info.st_uid, stat_info.st_gid,
                           os.access(path, os.R_OK), os.access(path, os.W_OK))
            else:
                logger.warning("  %s: does not exist", path)
        
        # Vérifier l'utilisateur actuel
        logger.info("Current user: uid=%d, gid=%d, name=%s", os.getuid(), os.getgid(), os.getenv('USER', 'unknown'))
        
        # Vérifier les variables d'environnement importantes
        import os as os_module
        env_vars = ['CKAN_INI', 'CKAN_STORAGE_PATH', 'CKAN_API_KEY', 'XLOADER_API_TOKEN']
        logger.info("Environment variables:")
        for var in env_vars:
            value = os_module.getenv(var, 'NOT SET')
            if 'TOKEN' in var or 'KEY' in var:
                value = '***' if value != 'NOT SET' else value
            logger.info("  %s=%s", var, value)
        
        logger.info("=" * 80)
        
        # Appeler la commande ckan avec tous les arguments
        # sys.argv[0] est le script wrapper, on passe le reste à ckan
        # Pour le worker RQ, on doit laisser stdout/stderr aller directement vers
        # stdout/stderr (qui sont déjà redirigés par supervisor) pour que les logs
        # du worker RQ soient visibles. On utilise os.execv pour remplacer le
        # processus actuel par le worker RQ, ce qui permet au worker de rester actif.
        
        # Pour les commandes interactives comme "jobs worker", on doit remplacer
        # le processus actuel pour que le worker reste actif
        if 'jobs' in sys.argv and 'worker' in sys.argv:
            # C'est un worker RQ, remplacer le processus actuel pour que le worker reste actif
            # IMPORTANT: Ne PAS utiliser --burst (le worker doit rester actif en continu)
            if '--burst' in sys.argv:
                logger.error("=" * 80)
                logger.error("ERREUR: --burst ne doit PAS être utilisé pour le worker RQ!")
                logger.error("Le worker doit rester actif en continu pour traiter les nouveaux jobs.")
                logger.error("Suppression de --burst des arguments...")
                logger.error("=" * 80)
                # Retirer --burst des arguments
                args_without_burst = [arg for arg in sys.argv[1:] if arg != '--burst']
                sys.argv = [sys.argv[0]] + args_without_burst
            
            logger.info("Starting RQ worker (replacing current process)...")
            logger.info("Arguments reçus: %s", sys.argv)
            logger.info("Arguments à passer à ckan: %s", sys.argv[1:])
            logger.info("Command: ckan %s", ' '.join(sys.argv[1:]))
            logger.info("IMPORTANT: Worker reste actif en continu (sans --burst)")
            
            # Vérifier que le binaire ckan existe
            if not os.path.exists('/usr/local/bin/ckan'):
                logger.error("ERREUR: /usr/local/bin/ckan n'existe pas!")
                sys.exit(1)
            
            # Vérifier que le binaire est exécutable
            if not os.access('/usr/local/bin/ckan', os.X_OK):
                logger.error("ERREUR: /usr/local/bin/ckan n'est pas exécutable!")
                sys.exit(1)
            
            # Construire la commande complète pour os.execv
            exec_args = ['ckan'] + sys.argv[1:]
            logger.info("Exécution de os.execv avec: %s", exec_args)
            logger.info("Commande complète: %s", ' '.join(exec_args))
            logger.info("Le worker RQ devrait maintenant démarrer et rester actif")
            # os.execv remplace le processus actuel, donc le code après ne sera jamais exécuté
            # C'est ce qu'on veut : le worker RQ doit rester actif et écouter les jobs
            # Si os.execv échoue, une exception sera levée et capturée par le try/except
            os.execv('/usr/local/bin/ckan', exec_args)
            # Ce code ne sera jamais atteint car os.execv remplace le processus
            # Si on arrive ici, c'est qu'os.execv a échoué
            logger.error("ERREUR: os.execv a échoué, le processus n'a pas été remplacé!")
            sys.exit(1)
        else:
            # Pour les autres commandes, capturer la sortie
            result = subprocess.run(['ckan'] + sys.argv[1:], check=False)
            logger.info("=" * 80)
            logger.info("CKAN CLI process exited with code: %d", result.returncode)
            logger.info("=" * 80)
            sys.exit(result.returncode)
    except KeyboardInterrupt:
        logger.info("Received keyboard interrupt, exiting...")
        sys.exit(0)
    except SystemExit as e:
        # Laisser SystemExit passer (pour les codes de sortie normaux)
        raise
    except Exception as e:
        logger.error("Error starting CKAN CLI: %s", e, exc_info=True)
        sys.exit(1)
PYTHON_WRAPPER

# Copy RQ worker wrapper that initializes CKAN before starting RQ worker
COPY rq_worker_wrapper.py /srv/app/rq_worker_wrapper.py

# Copy diagnostic script for CKAN initialization
COPY scripts/diagnose-ckan-init.py /srv/app/scripts/diagnose-ckan-init.py

# Auto-hébergement des assets externes des pages HTML pygeoapi (RGESN : pas de CDN).
# Télécharge Leaflet/markercluster/VectorGrid/Bootstrap/tablesort/… dans le static de
# pygeoapi et réécrit les templates pour pointer en local. Non bloquant.
COPY scripts/selfhost-pygeoapi-assets.py /srv/app/scripts/selfhost-pygeoapi-assets.py
RUN python3 /srv/app/scripts/selfhost-pygeoapi-assets.py || echo "self-host assets pygeoapi : non bloquant"

# Set executable permissions for all scripts in one command to reduce layers
RUN chmod +x /srv/app/test-keycloak-config.sh /srv/app/start_ckan_with_logs.sh /srv/app/start-supervisor.sh /srv/app/rq_worker_wrapper.py /srv/app/scripts/diagnose-ckan-init.py

# Create CKAN data directories with proper permissions (must be done as root before switching to ckan user)
RUN mkdir -p /var/lib/ckan/webassets /var/lib/ckan/storage /var/lib/ckan/resources && \
    chown -R ckan:ckan-sys /var/lib/ckan/

# Set the default command to use the wrapper script that configures SECRET_KEY before starting supervisor
CMD ["/srv/app/start-supervisor.sh"]

USER ckan

