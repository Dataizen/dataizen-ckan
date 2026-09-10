#!/bin/bash

# Script de démarrage CKAN en local
# Ce script vous guide pour lancer CKAN en local
#
# Options disponibles:
#   --erasedb        : Vider complètement la base de données locale
#   --nobuild        : Lancer les services sans reconstruire les images
#   --nocache        : Reconstruire toutes les images sans utiliser le cache
#   --logs           : Afficher les logs des services après démarrage
#   --add-test-dataset : Ajouter un dataset de test avec données géospatiales
#   --init-ogc       : Initialiser et synchroniser OGC après démarrage
#   --import-ckan    : Importer un dump CKAN (efface la BDD avant)
#   --import-datastore : Importer un dump Datastore (efface la BDD avant)
#   --todatagis      : Importer les fichiers géospatiaux CKAN vers datagis
#   --help           : Afficher cette aide

# Fonction pour garantir que ckan_admin existe toujours avec la bonne clé API
ensure_ckan_admin() {
    # echo "Vérification/création de ckan_admin avec API key..."
    
    # Générer le hash du mot de passe si nécessaire
    CKAN_ADMIN_HASH=$(docker compose exec -T ckan python3 -c "from passlib.hash import pbkdf2_sha256; print(pbkdf2_sha256.hash('ckan_admin123'))" 2>/dev/null | tr -d '\r\n')
    
    if [ -z "$CKAN_ADMIN_HASH" ]; then
        echo " Impossible de générer le hash du mot de passe, utilisation d'une méthode alternative..."
        # Méthode alternative : utiliser ckan user add si disponible
        docker compose exec ckan ckan -c /srv/app/ckan.ini user add ckan_admin email=ckan_admin@dataizen.eu password=ckan_admin123 fullname="CKAN Admin" 2>&1 || echo "Utilisateur peut-être déjà existant"
        docker compose exec -e PGPASSWORD=ckan ckan psql -h db -U ckan -d ckan -c "UPDATE \"user\" SET sysadmin = true, apikey = 'ckan-local-dev-apikey', state = 'active' WHERE name = 'ckan_admin';" 2>&1 || true
    else
        # Créer ou mettre à jour via SQL direct (plus fiable)
        docker compose exec -T db psql -U ckan -d ckan -v ckan_admin_hash="$CKAN_ADMIN_HASH" <<'SQL'
-- Créer ou mettre à jour l'utilisateur ckan_admin
INSERT INTO "user" (id, name, fullname, email, password, sysadmin, state, apikey, about, created)
VALUES (
    'ckan_admin',
    'ckan_admin',
    'CKAN Admin',
    'ckan_admin@dataizen.eu',
    :'ckan_admin_hash',
    true,
    'active',
    'ckan-local-dev-apikey',
    '',
    NOW()
)
ON CONFLICT (id) DO UPDATE SET 
    sysadmin = true,
    state = 'active',
    email = 'ckan_admin@dataizen.eu',
    apikey = 'ckan-local-dev-apikey',
    password = :'ckan_admin_hash';
SQL
    fi
    
    # Vérifier que l'utilisateur existe et a la bonne clé API
    API_KEY_CHECK=$(docker compose exec -e PGPASSWORD=ckan ckan psql -h db -U ckan -d ckan -t -c "SELECT apikey FROM \"user\" WHERE name = 'ckan_admin' AND state = 'active';" 2>/dev/null | tr -d ' \n')
    
    if [ "$API_KEY_CHECK" = "ckan-local-dev-apikey" ]; then
        # echo "ckan_admin existe et est configuré (API Key: ckan-local-dev-apikey)"
        return 0
    else
        echo " ckan_admin existe mais la clé API n'est pas correcte. Clé actuelle: $API_KEY_CHECK"
        # Forcer la mise à jour
        docker compose exec -e PGPASSWORD=ckan ckan psql -h db -U ckan -d ckan -c "UPDATE \"user\" SET apikey = 'ckan-local-dev-apikey', sysadmin = true, state = 'active' WHERE name = 'ckan_admin';" 2>&1 || true
        return 1
    fi
}

# Variables par défaut
ERASE_DB=false
NO_BUILD=false
NO_CACHE=false
SHOW_LOGS=false
ADD_TEST_DATASET=false
INIT_OGC=false
INIT_ENV=false
IMPORT_CKAN=""
IMPORT_DATASTORE=""
TO_DATAGIS=false

# Traitement des arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        --erasedb)
            ERASE_DB=true
            shift
            ;;
        --nobuild)
            NO_BUILD=true
            shift
            ;;
        --nocache)
            NO_CACHE=true
            shift
            ;;
        --logs)
            SHOW_LOGS=true
            shift
            ;;
        --add-test-dataset)
            ADD_TEST_DATASET=true
            shift
            ;;
        --init)
            INIT_ENV=true
            ERASE_DB=true
            shift
            ;;
        --init-ogc)
            INIT_OGC=true
            shift
            ;;
        --import-ckan)
            if [ -z "$2" ] || [[ "$2" == --* ]]; then
                echo "--import-ckan nécessite un chemin vers le fichier dump"
                exit 1
            fi
            IMPORT_CKAN="$2"
            ERASE_DB=true  # Effacer la BDD avant l'import
            shift 2
            ;;
        --import-datastore)
            if [ -z "$2" ] || [[ "$2" == --* ]]; then
                echo "--import-datastore nécessite un chemin vers le fichier dump"
                exit 1
            fi
            IMPORT_DATASTORE="$2"
            ERASE_DB=true  # Effacer la BDD avant l'import
            shift 2
            ;;
        --todatagis)
            TO_DATAGIS=true
            shift
            ;;
        --help)
            echo "Usage: $0 [OPTIONS]"
            echo ""
            echo "Options:"
            echo "  --erasedb        Vider complètement la base de données locale"
            echo "  --nobuild        Lancer les services sans reconstruire les images"
            echo "  --nocache        Reconstruire toutes les images sans utiliser le cache"
            echo "  --logs           Afficher les logs des services après démarrage"
            echo "  --add-test-dataset    Ajouter un dataset de test avec données géospatiales"
            echo "  --import-ckan FILE    Importer un dump CKAN (efface la BDD avant)"
            echo "  --import-datastore FILE Importer un dump Datastore (efface la BDD avant)"
            echo "  --todatagis      Importer les fichiers géospatiaux CKAN vers datagis"
            echo "  --help           Afficher cette aide"
            echo ""
            echo "Exemples:"
            echo "  $0                                    # Démarrage normal"
            echo "  $0 --logs                             # Démarrage avec affichage des logs"
            echo "  $0 --add-test-dataset                 # Ajouter un dataset de test géospatial"
            echo "  $0 --nocache                          # Reconstruction complète sans cache"
            echo "  $0 --nocache --logs                   # Reconstruction sans cache + logs"
            echo "  $0 --import-ckan dumps/ckan_20250124.sql"
            echo "  $0 --import-datastore dumps/datastore_20250124.sql"
            echo "  $0 --import-ckan dumps/ckan_20250124.sql --import-datastore dumps/datastore_20250124.sql"
            echo "  $0 --todatagis                          # Importer fichiers CKAN vers datagis"
            echo ""
            echo "Services géospatiaux:"
            echo "  - pygeoapi (OGC API): http://localhost:5001"
            echo "  - MapServer (WMS/WFS): http://localhost:8081"
            echo ""
            echo "Par défaut, le script redémarre les services s'ils sont déjà démarrés."
            exit 0
            ;;
        *)
            echo "Option inconnue: $1"
            echo "Utilisez --help pour voir les options disponibles."
            exit 1
            ;;
    esac
done

echo "=== Configuration CKAN Local ==="
echo ""

# Vérifier si Docker est installé
if ! command -v docker &> /dev/null; then
    echo "Docker n'est pas installé. Veuillez installer Docker Desktop."
    exit 1
fi

if ! docker compose version &> /dev/null; then
    echo "Docker Compose n'est pas installé. Veuillez installer Docker Compose."
    exit 1
fi

echo "Docker et Docker Compose sont installés"
echo ""

# Option --erasedb : Vider complètement la base de données
if [ "$ERASE_DB" = true ]; then
    echo " Suppression de la base de données locale..."
    
    # Arrêter les services s'ils tournent
    echo " Arrêt des services..."
    docker compose down 2>/dev/null || true
    
    # Supprimer le volume de données PostgreSQL
    echo " Suppression du volume de données PostgreSQL..."
    docker volume rm dataizen-ckan_pg_data 2>/dev/null || true
    rm -rf ./pg_data 2>/dev/null || true
    
    echo "Base de données locale supprimée"
    echo ""
fi

echo "=== Démarrage des services ==="

# Initialiser buildx pour amd64 si nécessaire (macOS M4 -> Ubuntu amd64)
setup_buildx() {
    if ! docker buildx ls | grep -q "amd64-builder"; then
        echo "Configuration de buildx pour amd64..."
        docker buildx create --name amd64-builder --driver docker-container --platform linux/amd64 --use 2>/dev/null || \
        docker buildx use amd64-builder 2>/dev/null || echo " buildx déjà configuré"
    else
        echo "buildx amd64-builder déjà configuré"
        docker buildx use amd64-builder 2>/dev/null || true
    fi
    docker buildx inspect --bootstrap 2>/dev/null || true
}

# Activer buildx et DOCKER_BUILDKIT pour forcer la compilation amd64
export DOCKER_BUILDKIT=1
export COMPOSE_DOCKER_CLI_BUILD=1
export DOCKER_DEFAULT_PLATFORM=linux/amd64

# Configurer buildx si on est sur macOS ARM
if [[ "$(uname -m)" == "arm64" ]] || [[ "$(uname -m)" == "aarch64" ]]; then
    echo "Détection macOS ARM, configuration de buildx pour amd64..."
    setup_buildx
fi

# Construire les images (sauf si --nobuild)
if [ "$NO_BUILD" = false ]; then
    if [ "$NO_CACHE" = true ]; then
        echo "Construction des images Docker SANS CACHE (amd64)..."
        docker compose build --no-cache --platform linux/amd64 ckan mapserver db
    else
        echo "Construction des images Docker (amd64)..."
        docker compose build --platform linux/amd64 ckan mapserver db
    fi
else
    echo " Construction des images ignorée (--nobuild)"
fi

# Démarrer/redémarrer les services
echo "Démarrage des services..."
if docker compose ps | grep -q "Up"; then
    echo "Arrêt des services existants pour utiliser la nouvelle image..."
    docker compose down
    echo "Démarrage avec la nouvelle image..."
    docker compose up -d
else
    echo "Démarrage des services..."
    docker compose up -d
fi

# Redémarrer pygeoapi explicitement pour s'assurer qu'il utilise la nouvelle configuration
if docker compose ps pygeoapi | grep -q "Up"; then
    echo "Redémarrage de pygeoapi pour charger la nouvelle configuration..."
    docker compose restart pygeoapi
    sleep 5
fi

echo ""
echo "=== Vérification des services ==="

# Attendre que les services soient prêts
echo "Attente du démarrage des services..."
sleep 20

# Vérifier les services
echo "Vérification des services:"

# Vérifier CKAN
if docker compose ps ckan | grep -q "Up"; then
    echo "CKAN est démarré"
else
    echo "CKAN n'est pas démarré"
fi

# Vérifier PostgreSQL
if docker compose ps db | grep -q "Up"; then
    echo "PostgreSQL est démarré"
else
    echo "PostgreSQL n'est pas démarré"
fi

# Vérifier Solr
if docker compose ps solr | grep -q "Up"; then
    echo "Solr est démarré"
else
    echo "Solr n'est pas démarré"
fi

# Vérifier Redis
if docker compose ps redis | grep -q "Up"; then
    echo "Redis est démarré"
else
    echo "Redis n'est pas démarré"
fi

# Vérifier et démarrer/redémarrer pygeoapi
if docker compose ps pygeoapi | grep -q "Up"; then
    echo "pygeoapi est démarré"
    echo "Redémarrage de pygeoapi pour charger la nouvelle configuration..."
    docker compose restart pygeoapi
    sleep 5
else
    echo "Démarrage de pygeoapi..."
    docker compose up -d pygeoapi
    sleep 5
fi

# Vérifier MapServer
if docker compose ps mapserver | grep -q "Up"; then
    echo "MapServer est démarré"
else
    echo " MapServer n'est pas démarré (optionnel pour le développement)"
fi

echo ""
echo "=== Configuration finale ==="

if [ "$INIT_ENV" = true ]; then
    echo "Réinitialisation complète de la base de données..."
    echo " Arrêt temporaire de CKAN..."
    docker compose stop ckan >/dev/null 2>&1 || true

    echo "Recréation des bases "ckan", "datastore" et "datagis"..."
    docker compose exec -T db psql -U postgres -d postgres <<'SQL'
SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname IN ('ckan','datastore','datagis') AND pid <> pg_backend_pid();
DROP DATABASE IF EXISTS ckan;
CREATE DATABASE ckan OWNER ckan ENCODING 'UTF8';
DROP DATABASE IF EXISTS datastore;
CREATE DATABASE datastore OWNER ckan ENCODING 'UTF8';
DROP DATABASE IF EXISTS datagis;
CREATE DATABASE datagis OWNER ckan ENCODING 'UTF8';
GRANT CONNECT ON DATABASE datastore TO datastore;
GRANT CONNECT ON DATABASE datastore TO datastore_ro;
SQL

    echo "Installation des extensions PostGIS..."
    docker compose exec -T db psql -U postgres -d ckan -c "CREATE EXTENSION IF NOT EXISTS postgis;" >/dev/null 2>&1
    docker compose exec -T db psql -U postgres -d ckan -c "CREATE EXTENSION IF NOT EXISTS postgis_topology;" >/dev/null 2>&1
    docker compose exec -T db psql -U postgres -d ckan -c "CREATE EXTENSION IF NOT EXISTS postgis_raster;" >/dev/null 2>&1
    docker compose exec -T db psql -U postgres -d ckan -c "CREATE EXTENSION IF NOT EXISTS fuzzystrmatch;" >/dev/null 2>&1
    docker compose exec -T db psql -U postgres -d ckan -c "CREATE EXTENSION IF NOT EXISTS postgis_tiger_geocoder;" >/dev/null 2>&1
    
    # Installer PostGIS dans datagis aussi
    echo "Installation des extensions PostGIS dans datagis..."
    docker compose exec -T db psql -U postgres -d datagis -c "CREATE EXTENSION IF NOT EXISTS postgis;" >/dev/null 2>&1
    docker compose exec -T db psql -U postgres -d datagis -c "CREATE EXTENSION IF NOT EXISTS postgis_topology;" >/dev/null 2>&1
    docker compose exec -T db psql -U postgres -d datagis -c "CREATE EXTENSION IF NOT EXISTS postgis_raster;" >/dev/null 2>&1

    echo "Redémarrage de CKAN..."
    docker compose start ckan >/dev/null 2>&1
    echo "Attente que CKAN soit complètement prêt..."
    sleep 20
fi

# Configurer les permissions de stockage
echo "Configuration des permissions de stockage..."
docker compose exec ckan chown -R ckan:ckan /var/lib/ckan/storage/ 2>/dev/null || echo "Permissions déjà configurées"

# Corriger les options dupliquées dans ckan.ini
#echo "Correction des options dupliquées dans ckan.ini..."
#docker compose exec ckan bash -c "
# Supprimer les lignes dupliquées
#sed -i '/^ckan\.datastore\.sql_search_enabled = true$/d' /srv/app/ckan.ini
#sed -i '/^ckan\.views\.available_views =/d' /srv/app/ckan.ini
# Réajouter les options une seule fois
#echo 'ckan.datastore.sql_search_enabled = true' >> /srv/app/ckan.ini
#echo 'ckan.views.available_views = recline_grid_view recline_graph_view recline_map_view' >> /srv/app/ckan.ini
#"

# Corriger le problème de migration Alembic si nécessaire
echo "Vérification/correction de l'état des migrations Alembic..."
docker compose cp ckan-app/ckan/fix-alembic-version.py ckan:/tmp/fix-alembic-version.py 2>/dev/null || echo "Script de correction non trouvé, continuons..."
docker compose exec ckan python3 /tmp/fix-alembic-version.py 2>/dev/null || echo "Correction Alembic ignorée (peut être normal si la base est vide)"

# Nettoyer les doublons dans la table user (pour permettre la création de l'index unique)
echo "Nettoyage des doublons dans la table user..."
docker compose cp ckan-app/ckan/fix-user-duplicates.py ckan:/tmp/fix-user-duplicates.py 2>/dev/null || echo "Script de nettoyage non trouvé, continuons..."
docker compose exec ckan python3 /tmp/fix-user-duplicates.py 2>/dev/null || echo "Nettoyage des doublons ignoré (peut être normal si la base est vide)"

# Initialiser la base de données CKAN
echo "Initialisation de la base de données CKAN..."
docker compose exec ckan ckan -c /srv/app/ckan.ini db init
if [ "$INIT_ENV" = true ]; then
    echo "Application des migrations Harvest..."
    docker compose exec -T ckan ckan -c /srv/app/ckan.ini db upgrade -p harvest >/dev/null 2>&1 || echo "Impossible d'exécuter db upgrade -p harvest"
fi

# Créer la table api_token si elle n'existe pas (nécessaire pour les tokens API)
echo "Vérification/création de la table system_info..."
docker compose exec -e PGPASSWORD=ckan ckan psql -h db -U ckan -d ckan <<'SQL' 2>/dev/null || echo "Table system_info peut déjà exister"
CREATE TABLE IF NOT EXISTS system_info (
    id SERIAL PRIMARY KEY,
    key VARCHAR(255) NOT NULL UNIQUE,
    value TEXT,
    state VARCHAR(50) DEFAULT 'active',
    created TIMESTAMP WITHOUT TIME ZONE DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_system_info_key ON system_info(key);
CREATE INDEX IF NOT EXISTS idx_system_info_state ON system_info(state);
SQL

echo "Vérification/création de la table api_token..."
docker compose exec -e PGPASSWORD=ckan ckan psql -h db -U ckan -d ckan <<'SQL' 2>/dev/null || echo "Table api_token peut déjà exister"
CREATE TABLE IF NOT EXISTS api_token (
    id VARCHAR(255) PRIMARY KEY,
    name VARCHAR(255) NOT NULL,
    user_id VARCHAR(255),
    created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW(),
    last_access TIMESTAMP WITHOUT TIME ZONE,
    plugin_extras JSONB DEFAULT '{}'::jsonb,
    CONSTRAINT api_token_user_id_fkey FOREIGN KEY (user_id) REFERENCES "user"(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_api_token_user_id ON api_token(user_id);
INSERT INTO system_info (key, value, state)
VALUES ('ckan.config_update', '0', 'active')
ON CONFLICT (key) DO NOTHING;
SQL

# Vérifier et créer la base datagis si elle n'existe pas
echo "Vérification/création de la base datagis..."
docker compose exec -T db psql -U postgres -d postgres <<'SQL' 2>/dev/null || echo "Erreur lors de la création de datagis"
SELECT 1 FROM pg_database WHERE datname = 'datagis';
SQL
DATAGIS_EXISTS=$(docker compose exec -T db psql -U postgres -d postgres -t -c "SELECT 1 FROM pg_database WHERE datname = 'datagis';" 2>/dev/null | tr -d ' \n')
if [ -z "$DATAGIS_EXISTS" ] || [ "$DATAGIS_EXISTS" != "1" ]; then
    echo "Création de la base datagis..."
    docker compose exec -T db psql -U postgres -d postgres <<'SQL'
CREATE DATABASE datagis OWNER ckan ENCODING 'UTF8';
SQL
    echo "Installation des extensions PostGIS dans datagis..."
    docker compose exec -T db psql -U postgres -d datagis -c "CREATE EXTENSION IF NOT EXISTS postgis;" >/dev/null 2>&1
    docker compose exec -T db psql -U postgres -d datagis -c "CREATE EXTENSION IF NOT EXISTS postgis_topology;" >/dev/null 2>&1
    docker compose exec -T db psql -U postgres -d datagis -c "CREATE EXTENSION IF NOT EXISTS postgis_raster;" >/dev/null 2>&1
    echo "Base datagis créée avec PostGIS"
else
    echo "Base datagis existe déjà"
fi

# Attendre que CKAN soit complètement démarré
echo "Attente du démarrage complet de CKAN..."
sleep 10

# Vérifier si la base de données contient déjà des données
echo "Vérification du contenu de la base de données..."
# Compter les utilisateurs sauf 'default' (créé automatiquement par CKAN)
USER_COUNT=$(docker compose exec -e PGPASSWORD=ckan ckan psql -h db -U ckan -d ckan -t -c "SELECT COUNT(*) FROM \"user\" WHERE name != 'default';" 2>/dev/null | tr -d ' ' || echo "0")
ORG_COUNT=$(docker compose exec -e PGPASSWORD=ckan ckan psql -h db -U ckan -d ckan -t -c "SELECT COUNT(*) FROM \"group\" WHERE type='organization';" 2>/dev/null | tr -d ' ' || echo "0")

# Logique d'import des dumps
if [ -n "$IMPORT_CKAN" ] || [ -n "$IMPORT_DATASTORE" ]; then
    echo "Import des dumps spécifiés..."
    
    # Import du dump CKAN
    if [ -n "$IMPORT_CKAN" ]; then
        if [ -f "$IMPORT_CKAN" ]; then
            echo "Fichier $IMPORT_CKAN trouvé"
            
            
            # Détecter le format du dump automatiquement
            echo "Détection du format du dump..."
            DUMP_EXTENSION="${IMPORT_CKAN##*.}"
            DUMP_BASENAME=$(basename "$IMPORT_CKAN")
            
            # Copier le dump dans le conteneur db avec le nom original
            docker cp "$IMPORT_CKAN" db:/tmp/$DUMP_BASENAME
            
            # Corriger les permissions du fichier
            docker compose exec db chmod 644 /tmp/$DUMP_BASENAME
            
            # Détecter le format par l'en-tête magique PGDMP
            DUMP_TYPE=$(docker compose exec db head -c 5 /tmp/$DUMP_BASENAME | od -c | grep -q "P G D M P" && echo "custom" || echo "text")
            
            if [ "$DUMP_TYPE" = "custom" ]; then
                echo "Format binaire détecté, utilisation de pg_restore..."
                docker compose exec -e PGPASSWORD=ckan db pg_restore -h localhost -U ckan -d ckan --clean --if-exists --no-owner --no-privileges --disable-triggers --single-transaction /tmp/$DUMP_BASENAME
            else
                echo "Format texte détecté, utilisation de psql..."
                # Arrêter temporairement CKAN pour libérer les connexions
                echo " Arrêt temporaire de CKAN pour libérer les connexions..."
                docker compose stop ckan
                
                # Attendre que les connexions se ferment
                sleep 5
                
                # Nettoyer la base avant l'import (avec gestion d'erreur)
                echo " Nettoyage de la base de données..."
                docker compose exec db psql -U ckan -d ckan -c "
                DO \$\$
                BEGIN
                    -- Terminer toutes les connexions actives sauf la nôtre
                    PERFORM pg_terminate_backend(pid) 
                    FROM pg_stat_activity 
                    WHERE datname = 'ckan' AND pid <> pg_backend_pid();
                    
                    -- Attendre un peu
                    PERFORM pg_sleep(2);
                END
                \$\$;
                DROP SCHEMA IF EXISTS public CASCADE; 
                CREATE SCHEMA public;
                GRANT ALL ON SCHEMA public TO ckan;
                GRANT ALL ON SCHEMA public TO public;
                " || echo " Nettoyage partiel de la base"
                
                # Importer le dump
                echo "Import du dump en cours..."
                docker compose exec db psql -U ckan -d ckan -v ON_ERROR_STOP=1 -v client_min_messages=warning -f /tmp/$DUMP_BASENAME
                
                # Redémarrer CKAN
                echo "Redémarrage de CKAN..."
                docker compose start ckan
            fi
            
            if [ $? -eq 0 ]; then
                echo "Dump CKAN restauré avec succès"
                
                # Créer la table system_info si elle n'existe pas (peut manquer dans certains dumps)
                echo "Vérification/création de la table system_info après import..."
                docker compose exec -e PGPASSWORD=ckan ckan psql -h db -U ckan -d ckan <<'SQL' 2>/dev/null || echo "Table system_info peut déjà exister"
CREATE TABLE IF NOT EXISTS system_info (
    id SERIAL PRIMARY KEY,
    key VARCHAR(255) NOT NULL UNIQUE,
    value TEXT,
    state VARCHAR(50) DEFAULT 'active',
    created TIMESTAMP WITHOUT TIME ZONE DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_system_info_key ON system_info(key);
CREATE INDEX IF NOT EXISTS idx_system_info_state ON system_info(state);
SQL

                # Créer la table api_token si elle n'existe pas (peut manquer dans certains dumps)
                echo "Vérification/création de la table api_token après import..."
                docker compose exec -e PGPASSWORD=ckan ckan psql -h db -U ckan -d ckan <<'SQL' 2>/dev/null || echo "Table api_token peut déjà exister"
CREATE TABLE IF NOT EXISTS api_token (
    id VARCHAR(255) PRIMARY KEY,
    name VARCHAR(255) NOT NULL,
    user_id VARCHAR(255),
    created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW(),
    last_access TIMESTAMP WITHOUT TIME ZONE,
    plugin_extras JSONB DEFAULT '{}'::jsonb,
    CONSTRAINT api_token_user_id_fkey FOREIGN KEY (user_id) REFERENCES "user"(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_api_token_user_id ON api_token(user_id);
INSERT INTO system_info (key, value, state)
VALUES ('ckan.config_update', '0', 'active')
ON CONFLICT (key) DO NOTHING;
SQL
                
                # Garantir que ckan_admin existe avec la bonne clé API après import
                ensure_ckan_admin
            else
                echo "Erreur lors de la restauration du dump CKAN"
            fi
            
            # Nettoyer le fichier temporaire
            docker compose exec db rm -f /tmp/$DUMP_BASENAME
        else
            echo "Fichier $IMPORT_CKAN non trouvé"
            exit 1
        fi
    fi
    
    # Import du dump Datastore
    if [ -n "$IMPORT_DATASTORE" ]; then
        if [ -f "$IMPORT_DATASTORE" ]; then
            echo "Fichier $IMPORT_DATASTORE trouvé"
            
            
            # Détecter le format du dump automatiquement
            echo "Détection du format du dump..."
            DUMP_EXTENSION="${IMPORT_DATASTORE##*.}"
            DUMP_BASENAME=$(basename "$IMPORT_DATASTORE")
            
            # Copier le dump dans le conteneur db avec le nom original
            docker cp "$IMPORT_DATASTORE" db:/tmp/$DUMP_BASENAME
            
            # Corriger les permissions du fichier
            docker compose exec db chmod 644 /tmp/$DUMP_BASENAME
            
            # Détecter le format par l'en-tête magique PGDMP
            DUMP_TYPE=$(docker compose exec db head -c 5 /tmp/$DUMP_BASENAME | od -c | grep -q "P G D M P" && echo "custom" || echo "text")
            
            if [ "$DUMP_TYPE" = "custom" ]; then
                echo "Format binaire détecté, utilisation de pg_restore..."
                docker compose exec -e PGPASSWORD=ckan db pg_restore -h localhost -U ckan -d datastore --clean --if-exists --no-owner --no-privileges --disable-triggers --single-transaction /tmp/$DUMP_BASENAME
            else
                echo "Format texte détecté, utilisation de psql..."
                # Arrêter temporairement CKAN pour libérer les connexions
                echo " Arrêt temporaire de CKAN pour libérer les connexions..."
                docker compose stop ckan
                
                # Attendre que les connexions se ferment
                sleep 5
                
                # Nettoyer la base avant l'import (avec gestion d'erreur)
                echo " Nettoyage de la base de données datastore..."
                docker compose exec db psql -U ckan -d datastore -c "
                DO \$\$
                BEGIN
                    -- Terminer toutes les connexions actives sauf la nôtre
                    PERFORM pg_terminate_backend(pid) 
                    FROM pg_stat_activity 
                    WHERE datname = 'datastore' AND pid <> pg_backend_pid();
                    
                    -- Attendre un peu
                    PERFORM pg_sleep(2);
                END
                \$\$;
                DROP SCHEMA IF EXISTS public CASCADE; 
                CREATE SCHEMA public;
                GRANT ALL ON SCHEMA public TO ckan;
                GRANT ALL ON SCHEMA public TO public;
                " || echo " Nettoyage partiel de la base datastore"
                
                # Importer le dump
                echo "Import du dump datastore en cours..."
                docker compose exec db psql -U ckan -d datastore -v ON_ERROR_STOP=1 -v client_min_messages=warning -f /tmp/$DUMP_BASENAME
                
                # Redémarrer CKAN
                echo "Redémarrage de CKAN..."
                docker compose start ckan
            fi
            
            if [ $? -eq 0 ]; then
                echo "Dump Datastore restauré avec succès"
            else
                echo "Erreur lors de la restauration du dump Datastore"
            fi
            
            # Nettoyer le fichier temporaire
            docker compose exec db rm -f /tmp/$DUMP_BASENAME
        else
            echo "Fichier $IMPORT_DATASTORE non trouvé"
            exit 1
        fi
    fi
    
    # Les corrections de colonnes sont maintenant faites systématiquement au démarrage
        
        # Correction des tables manquantes pour les extensions
        echo "Correction des tables manquantes pour les extensions..."
        docker compose exec -e PGPASSWORD=ckan ckan psql -h db -U ckan -d ckan <<'SQL'
        CREATE TABLE IF NOT EXISTS harvest_object (
            id TEXT PRIMARY KEY,
            guid TEXT,
            current BOOLEAN,
            gathered TIMESTAMP,
            fetch_started TIMESTAMP,
            content TEXT,
            fetch_finished TIMESTAMP,
            import_started TIMESTAMP,
            import_finished TIMESTAMP,
            state TEXT,
            metadata_modified_date TIMESTAMP,
            retry_times INTEGER,
            harvest_job_id TEXT,
            harvest_source_id TEXT,
            package_id TEXT,
            report_status TEXT
        );
        
        -- Créer la table api_token si elle n'existe pas
        CREATE TABLE IF NOT EXISTS api_token (
            id VARCHAR(255) PRIMARY KEY,
            name VARCHAR(255) NOT NULL,
            user_id VARCHAR(255),
            created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW(),
            last_access TIMESTAMP WITHOUT TIME ZONE,
            plugin_extras JSONB DEFAULT '{}'::jsonb,
            CONSTRAINT api_token_user_id_fkey FOREIGN KEY (user_id) REFERENCES "user"(id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_api_token_user_id ON api_token(user_id);
        
        -- Créer la table system_info si elle n'existe pas
        CREATE TABLE IF NOT EXISTS system_info (
            id VARCHAR(255) PRIMARY KEY,
            key VARCHAR(255) NOT NULL UNIQUE,
            value TEXT,
            state VARCHAR(50) DEFAULT 'active',
            created TIMESTAMP WITHOUT TIME ZONE DEFAULT NOW()
        );
        CREATE INDEX IF NOT EXISTS idx_system_info_key ON system_info(key);
        CREATE INDEX IF NOT EXISTS idx_system_info_state ON system_info(state);
        
        -- Créer l'entrée system_info pour ckan.config_update si elle n'existe pas
        INSERT INTO system_info (id, key, value, state)
        VALUES ('ckan.config_update', 'ckan.config_update', '0', 'active')
        ON CONFLICT (key) DO NOTHING;
SQL
        echo "Tables d'extensions créées"
        
        # Configuration des permissions après import des dumps
        echo "Configuration des permissions PostgreSQL..."
        
        # Permissions pour le rôle datastore_ro
        echo "Configuration des permissions datastore_ro..."
        docker compose exec -e PGPASSWORD=ckan ckan psql -h db -U ckan -d datastore -c "GRANT USAGE ON SCHEMA pg_catalog TO datastore_ro;" 2>/dev/null || echo "Permission pg_catalog ignorée"
        docker compose exec -e PGPASSWORD=ckan ckan psql -h db -U ckan -d datastore -c "GRANT USAGE ON SCHEMA information_schema TO datastore_ro;" 2>/dev/null || echo "Permission information_schema ignorée"
        docker compose exec -e PGPASSWORD=ckan ckan psql -h db -U ckan -d datastore -c "GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA pg_catalog TO datastore_ro;" 2>/dev/null || echo "Permission fonctions système ignorée"
        docker compose exec -e PGPASSWORD=ckan ckan psql -h db -U ckan -d datastore -c "ALTER DEFAULT PRIVILEGES IN SCHEMA pg_catalog GRANT EXECUTE ON FUNCTIONS TO datastore_ro;" 2>/dev/null || echo "Permission par défaut ignorée"
        
        echo "Permissions configurées"
    
    # Réindexation après import des dumps
    if [ -n "$IMPORT_CKAN" ] || [ -n "$IMPORT_DATASTORE" ]; then
        echo "Réindexation de l'index de recherche CKAN après import..."
        
        # Vérifier le nombre de packages importés
        PACKAGE_COUNT=$(docker compose exec -e PGPASSWORD=ckan ckan psql -h db -U ckan -d ckan -t -c "SELECT COUNT(*) FROM package WHERE state = 'active';" 2>/dev/null | tr -d ' \n' || echo "0")
        echo "Packages trouvés dans la base : $PACKAGE_COUNT"
        
        # Réindexer l'index de recherche CKAN (obligatoire après import)
        echo "Reconstruction de l'index de recherche CKAN..."
        docker compose exec ckan ckan -c /srv/app/ckan.ini search-index rebuild
        
        if [ $? -eq 0 ]; then
            echo "Index de recherche CKAN reconstruit avec succès"
        else
            echo "Erreur lors de la reconstruction de l'index de recherche"
        fi
        
        echo "Réindexation terminée"
        
        # Création du compte admin dataizendev
        echo "Création du compte admin dataizendev..."
        docker compose exec ckan ckan -c /srv/app/ckan.ini user add dataizendev email=dataizendev@example.org password=dataizendev11 fullname="Dataizen Dev Admin" 2>/dev/null || echo "Utilisateur dataizendev existe déjà"
        
        # Rendre l'utilisateur dataizendev sysadmin
        echo "Attribution des droits sysadmin..."
        docker compose exec -e PGPASSWORD=ckan ckan psql -h db -U ckan -d ckan -c "UPDATE \"user\" SET sysadmin = true WHERE name = 'dataizendev';" 2>/dev/null || echo "Mise à jour sysadmin ignorée"
        
        echo "Compte admin dataizendev créé et configuré"
        
        # Création du compte admin ckan_admin pour Drupal
        echo "Création du compte admin ckan_admin pour Drupal..."
        
        # Attendre que CKAN soit prêt
        sleep 3
        
        # Vérifier si l'utilisateur existe déjà
        CKAN_ADMIN_EXISTS=$(docker compose exec -e PGPASSWORD=ckan ckan psql -h db -U ckan -d ckan -t -c "SELECT COUNT(*) FROM \"user\" WHERE name = 'ckan_admin';" 2>/dev/null | tr -d ' \n' || echo "0")
        
        if [ "$CKAN_ADMIN_EXISTS" = "0" ]; then
            # Créer l'utilisateur (afficher les erreurs pour diagnostic)
            echo "Création de l'utilisateur ckan_admin..."
            CREATE_OUTPUT=$(docker compose exec ckan ckan -c /srv/app/ckan.ini user add ckan_admin email=ckan_admin@dataizen.eu password=ckan_admin123 fullname="CKAN Admin" 2>&1)
            CREATE_EXIT=$?
            
            if [ $CREATE_EXIT -ne 0 ]; then
                echo " Erreur lors de la création de ckan_admin:"
                echo "$CREATE_OUTPUT"
                # Réessayer après un délai
                echo "Nouvelle tentative après 5 secondes..."
                sleep 5
                docker compose exec ckan ckan -c /srv/app/ckan.ini user add ckan_admin email=ckan_admin@dataizen.eu password=ckan_admin123 fullname="CKAN Admin" 2>&1 || echo " Échec de la création de ckan_admin"
            fi
        else
            echo "Utilisateur ckan_admin existe déjà"
        fi
        
        # Vérifier à nouveau que l'utilisateur existe avant de mettre à jour
        CKAN_ADMIN_EXISTS_AFTER=$(docker compose exec -e PGPASSWORD=ckan ckan psql -h db -U ckan -d ckan -t -c "SELECT COUNT(*) FROM \"user\" WHERE name = 'ckan_admin';" 2>/dev/null | tr -d ' \n' || echo "0")
        
        if [ "$CKAN_ADMIN_EXISTS_AFTER" = "1" ]; then
            # Rendre l'utilisateur ckan_admin sysadmin et définir l'API key
            echo "Configuration de ckan_admin avec API key pour Drupal..."
            UPDATE_RESULT=$(docker compose exec -e PGPASSWORD=ckan ckan psql -h db -U ckan -d ckan -c "UPDATE \"user\" SET sysadmin = true, apikey = 'ckan-local-dev-apikey' WHERE name = 'ckan_admin';" 2>&1)
            UPDATE_EXIT=$?
            
            if [ $UPDATE_EXIT -eq 0 ]; then
                # Vérifier que l'API key a bien été définie
                API_KEY_CHECK=$(docker compose exec -e PGPASSWORD=ckan ckan psql -h db -U ckan -d ckan -t -c "SELECT apikey FROM \"user\" WHERE name = 'ckan_admin';" 2>/dev/null | tr -d ' \n')
                if [ "$API_KEY_CHECK" = "ckan-local-dev-apikey" ]; then
                    echo "Compte admin ckan_admin créé et configuré (API Key: ckan-local-dev-apikey)"
                else
                    echo " L'API key n'a pas été correctement définie. API key actuelle: $API_KEY_CHECK"
                fi
            else
                echo " Erreur lors de la mise à jour de ckan_admin:"
                echo "$UPDATE_RESULT"
            fi
        else
            echo "Impossible de configurer ckan_admin: l'utilisateur n'existe toujours pas"
        fi
        
        # Garantir que ckan_admin existe avec la bonne clé API après import
        ensure_ckan_admin
        
        # Création de l'organisation dataizen-dev si elle n'existe pas
        ORG_EXISTS=$(docker compose exec -e PGPASSWORD=ckan ckan psql -h db -U ckan -d ckan -t -c "SELECT COUNT(*) FROM \"group\" WHERE name = 'dataizen-dev' AND type = 'organization';" 2>/dev/null | tr -d ' \n' || echo "0")
        
        if [ "$ORG_EXISTS" = "0" ]; then
            echo "Création de l'organisation dataizen-dev..."
            docker compose exec -T db psql -U ckan -d ckan <<'SQL'
INSERT INTO "group" (id, name, title, description, type, is_organization, approval_status, state, created)
VALUES (
    'dataizen-dev',
    'dataizen-dev',
    'Dataizen Development',
    'Organisation de développement Dataizen pour les tests et le développement local',
    'organization',
    true,
    'approved',
    'active',
    NOW()
)
ON CONFLICT (id) DO UPDATE SET 
    state = 'active',
    is_organization = true;
SQL
            echo "Organisation dataizen-dev créée"
            
            # Ajouter les membres à l'organisation
            echo "Ajout des membres à l'organisation..."
            docker compose exec -T db psql -U ckan -d ckan <<'SQL'
INSERT INTO member (id, table_id, table_name, capacity, group_id)
SELECT 
    name || '-dataizen-dev' as id,
    name as table_id,
    'user' as table_name,
    'admin' as capacity,
    'dataizen-dev' as group_id
FROM "user"
WHERE name IN ('admin', 'ckan_admin', 'dataizendev')
AND sysadmin = true
ON CONFLICT (id) DO NOTHING;
SQL
            echo "Membres ajoutés à l'organisation"
        else
            echo "Organisation dataizen-dev existe déjà"
        fi
        
        # Correction de l'URL de base pour éviter les redirections vers le mauvais port
        echo "Correction de l'URL de base CKAN..."
        docker compose exec ckan sudo sed -i 's|ckan\.site_url = http://localhost:5000|ckan.site_url = http://localhost:8080|' /srv/app/ckan.ini
        echo "URL de base corrigée vers localhost:8080"
        
        # Note: La correction permanente est maintenant dans docker-compose.yml avec CKAN_SITE_URL=http://localhost:8080
    fi
    
elif [ "$USER_COUNT" -gt 0 ] || [ "$ORG_COUNT" -gt 0 ]; then
    echo "Base de données déjà peuplée ($USER_COUNT utilisateurs, $ORG_COUNT organisations)"
    echo "Pas de restauration du dump nécessaire"
else
    echo "Restauration du dump CKAN Dataizen par défaut..."
    if [ -f "dumps/ckan.dump" ]; then
        echo "Fichier dumps/ckan.dump trouvé"
        
        # Copier le dump dans le conteneur
        docker cp dumps/ckan.dump ckan:/tmp/ckan.dump
        
        # Restaurer le dump
        echo "Restauration en cours..."
        # Rediriger la sortie pour éviter le blocage du buffer et ajouter les options nécessaires
        # Utiliser -T pour désactiver le TTY et éviter les problèmes de buffer
        docker compose exec -T -e PGPASSWORD=ckan ckan pg_restore -h db -U ckan -d ckan --clean --if-exists --no-owner --no-privileges --single-transaction --no-verbose /tmp/ckan.dump > /dev/null 2>&1
        RESTORE_STATUS=$?
        
        if [ $RESTORE_STATUS -ne 0 ]; then
            echo "Erreur lors de la restauration, nouvelle tentative avec affichage des erreurs..."
            docker compose exec -T -e PGPASSWORD=ckan ckan pg_restore -h db -U ckan -d ckan --clean --if-exists --no-owner --no-privileges --single-transaction /tmp/ckan.dump 2>&1 | tail -30
            RESTORE_STATUS=$?
            if [ $RESTORE_STATUS -ne 0 ]; then
                echo "Échec de la restauration du dump"
                exit 1
            fi
        fi
        
        if [ $RESTORE_STATUS -eq 0 ]; then
            echo "Dump CKAN restauré avec succès"
            
            # Garantir que ckan_admin existe avec la bonne clé API après restauration
            ensure_ckan_admin
            
            # Création de l'organisation dataizen-dev si elle n'existe pas
            echo "Vérification/création de l'organisation dataizen-dev..."
            ORG_EXISTS=$(docker compose exec -e PGPASSWORD=ckan ckan psql -h db -U ckan -d ckan -t -c "SELECT COUNT(*) FROM \"group\" WHERE name = 'dataizen-dev' AND type = 'organization';" 2>/dev/null | tr -d ' \n' || echo "0")
            
            if [ "$ORG_EXISTS" = "0" ]; then
                echo "Création de l'organisation dataizen-dev..."
                docker compose exec -T db psql -U ckan -d ckan <<'SQL'
INSERT INTO "group" (id, name, title, description, type, is_organization, approval_status, state, created)
VALUES (
    'dataizen-dev',
    'dataizen-dev',
    'Dataizen Development',
    'Organisation de développement Dataizen pour les tests et le développement local',
    'organization',
    true,
    'approved',
    'active',
    NOW()
)
ON CONFLICT (id) DO UPDATE SET 
    state = 'active',
    is_organization = true;
SQL
                echo "Organisation dataizen-dev créée"
                
                # Ajouter ckan_admin comme membre de l'organisation
                echo "Ajout de ckan_admin à l'organisation..."
                docker compose exec -T db psql -U ckan -d ckan <<'SQL'
INSERT INTO member (id, table_id, table_name, capacity, group_id)
VALUES ('ckan_admin-dataizen-dev', 'ckan_admin', 'user', 'admin', 'dataizen-dev')
ON CONFLICT (id) DO NOTHING;
SQL
                echo "ckan_admin ajouté à l'organisation"
            else
                echo "Organisation dataizen-dev existe déjà"
            fi
        else
            echo "Erreur lors de la restauration du dump"
            echo "Utilisation de la configuration par défaut"
        fi
        
        # Nettoyer le fichier temporaire
        docker compose exec ckan rm -f /tmp/ckan.dump
    else
        echo "Fichier dumps/ckan.dump non trouvé"
        echo "Utilisation de la configuration par défaut"
    fi
fi

# Si la base était vide et qu'on n'a pas de dump, créer les utilisateurs et organisation
if [ "$USER_COUNT" -eq 0 ] && [ "$ORG_COUNT" -eq 0 ] && [ ! -f "dumps/ckan.dump" ] && [ -z "$IMPORT_CKAN" ] && [ "$INIT_ENV" = false ]; then
    # Créer les utilisateurs administrateurs seulement si pas de dump
    echo "Création des utilisateurs administrateurs..."
    docker compose exec ckan ckan -c /srv/app/ckan.ini sysadmin add admin admin123 admin@dataizen.eu || echo "Utilisateur admin déjà existant"
    
    # Garantir que ckan_admin existe avec la bonne clé API
    ensure_ckan_admin
    
    # Créer l'organisation dataizen-dev manuellement
    echo "Création de l'organisation dataizen-dev..."
    docker compose exec ckan python3 -c "
import psycopg2
from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT
import time

time.sleep(5)

try:
    conn = psycopg2.connect(
        host='db',
        port=5432,
        user='ckan',
        password='ckan',
        database='ckan'
    )
    conn.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
    cursor = conn.cursor()
    
    # Créer l'organisation dataizen-dev
    cursor.execute(\"\"\"
        INSERT INTO \"group\" (id, name, title, description, type, is_organization, approval_status, state)
        VALUES ('dataizen-dev', 'dataizen-dev', 'Dataizen Development', 'Organisation de développement Dataizen pour les tests et le développement local', 'organization', true, 'approved', 'active')
        ON CONFLICT (name) DO UPDATE SET state = 'active';
    \"\"\")
    
    # Ajouter l'admin comme membre de l'organisation
    cursor.execute(\"\"\"
        INSERT INTO member (id, table_id, table_name, capacity, group_id)
        VALUES ('admin-dataizen-dev', 'admin', 'user', 'admin', 'dataizen-dev')
        ON CONFLICT (id) DO NOTHING;
    \"\"\")
    
    conn.commit()
    print('Organisation dataizen-dev créée')
    
except Exception as e:
    print(f'Erreur: {e}')
finally:
    if 'conn' in locals():
        conn.close()
"
fi

# Configurer les permissions du datastore
echo "Configuration des permissions du datastore..."
docker compose exec -T ckan ckan -c /srv/app/ckan.ini datastore set-permissions | docker compose exec -T db psql "postgresql://postgres:postgres@db/datastore" >/dev/null 2>&1 || echo "Permissions datastore déjà configurées"

# Garantir que ckan_admin existe toujours (quel que soit le chemin d'exécution)
ensure_ckan_admin

if [ "$INIT_ENV" = true ]; then
    echo "Création des comptes administrateurs et de l'organisation..."
    
    # Attendre que la base soit accessible
    echo "Vérification que la base de données est accessible..."
    MAX_RETRIES=30
    RETRY_COUNT=0
    DB_READY=false
    
    while [ $RETRY_COUNT -lt $MAX_RETRIES ]; do
        if docker compose exec -T db psql -U ckan -d ckan -c "SELECT 1;" >/dev/null 2>&1; then
            DB_READY=true
            break
        fi
        RETRY_COUNT=$((RETRY_COUNT + 1))
        sleep 2
        echo "   Tentative $RETRY_COUNT/$MAX_RETRIES..."
    done
    
    if [ "$DB_READY" = false ]; then
        echo "La base de données n'est pas accessible après $MAX_RETRIES tentatives. Arrêt."
        exit 1
    fi
    
    echo "Base de données accessible, création des utilisateurs via SQL..."
    
    # Générer les hashs de mots de passe avec Python dans le conteneur CKAN
    echo "Génération des hashs de mots de passe..."
    ADMIN_HASH=$(docker compose exec -T ckan python3 -c "from passlib.hash import pbkdf2_sha256; print(pbkdf2_sha256.hash('admin123'))" 2>/dev/null | tr -d '\r\n')
    DATAIZENDEV_HASH=$(docker compose exec -T ckan python3 -c "from passlib.hash import pbkdf2_sha256; print(pbkdf2_sha256.hash('dataizendev11'))" 2>/dev/null | tr -d '\r\n')
    CKAN_ADMIN_HASH=$(docker compose exec -T ckan python3 -c "from passlib.hash import pbkdf2_sha256; print(pbkdf2_sha256.hash('ckan_admin123'))" 2>/dev/null | tr -d '\r\n')
    
    if [ -z "$ADMIN_HASH" ] || [ -z "$DATAIZENDEV_HASH" ] || [ -z "$CKAN_ADMIN_HASH" ]; then
        echo "Erreur lors de la génération des hashs de mots de passe"
        exit 1
    fi
    
    # Créer les utilisateurs directement via SQL avec les vrais hashs (évite les problèmes de transaction CKAN)
    echo "Création des utilisateurs..."
    docker compose exec -T db psql -U ckan -d ckan -v admin_hash="$ADMIN_HASH" -v dataizendev_hash="$DATAIZENDEV_HASH" -v ckan_admin_hash="$CKAN_ADMIN_HASH" <<'SQL'
-- Créer l'utilisateur admin
INSERT INTO "user" (id, name, fullname, email, password, sysadmin, state, about, created)
VALUES (
    'admin',
    'admin',
    'Administrator',
    'admin@dataizen.eu',
    :'admin_hash',
    true,
    'active',
    '',
    NOW()
)
ON CONFLICT (id) DO UPDATE SET 
    sysadmin = true,
    state = 'active',
    email = 'admin@dataizen.eu',
    password = :'admin_hash';
    
-- Créer l'utilisateur dataizendev
INSERT INTO "user" (id, name, fullname, email, password, sysadmin, state, about, created)
VALUES (
    'dataizendev',
    'dataizendev',
    'Dataizen Dev Admin',
    'dataizendev@example.org',
    :'dataizendev_hash',
    true,
    'active',
    '',
    NOW()
)
ON CONFLICT (id) DO UPDATE SET 
    sysadmin = true,
    state = 'active',
    email = 'dataizendev@example.org',
    password = :'dataizendev_hash';
    
-- Créer l'utilisateur ckan_admin
INSERT INTO "user" (id, name, fullname, email, password, sysadmin, state, apikey, about, created)
VALUES (
    'ckan_admin',
    'ckan_admin',
    'CKAN Admin',
    'ckan_admin@dataizen.eu',
    :'ckan_admin_hash',
    true,
    'active',
    'ckan-local-dev-apikey',
    '',
    NOW()
)
ON CONFLICT (id) DO UPDATE SET 
    sysadmin = true,
    state = 'active',
    email = 'ckan_admin@dataizen.eu',
    apikey = 'ckan-local-dev-apikey',
    password = :'ckan_admin_hash';
SQL
    echo "   Utilisateurs créés/mis à jour"
    
    # Créer l'organisation via SQL
    echo "Création de l'organisation dataizen-dev..."
    docker compose exec -T db psql -U ckan -d ckan <<'SQL'
INSERT INTO "group" (id, name, title, description, type, is_organization, approval_status, state, created)
VALUES (
    'dataizen-dev',
    'dataizen-dev',
    'Dataizen Development',
    'Organisation de développement Dataizen pour les tests et le développement local',
    'organization',
    true,
    'approved',
    'active',
    NOW()
)
ON CONFLICT (id) DO UPDATE SET 
    state = 'active',
    is_organization = true;
SQL
    echo "   Organisation dataizen-dev créée/mise à jour"
    
    # Garantir que ckan_admin existe avec la bonne clé API après création des utilisateurs
    ensure_ckan_admin
    
    # Ajouter les membres à l'organisation via SQL
    echo "Ajout des membres à l'organisation..."
    docker compose exec -T db psql -U ckan -d ckan <<'SQL'
INSERT INTO member (id, table_id, table_name, capacity, group_id)
VALUES 
    ('admin-dataizen-dev', 'admin', 'user', 'admin', 'dataizen-dev'),
    ('ckan_admin-dataizen-dev', 'ckan_admin', 'user', 'admin', 'dataizen-dev'),
    ('dataizendev-dataizen-dev', 'dataizendev', 'user', 'admin', 'dataizen-dev')
ON CONFLICT (id) DO NOTHING;
SQL
    echo "   Membres ajoutés à l'organisation"
    
    # Redémarrer CKAN pour recharger les utilisateurs
    echo "Redémarrage de CKAN pour recharger les utilisateurs..."
    docker compose restart ckan >/dev/null 2>&1
    echo "Attente que CKAN redémarre..."
    sleep 15
    
    echo "Initialisation de l'environnement de développement terminée"
fi

# Correction des colonnes manquantes (pour tous les démarrages)
echo "Correction des colonnes manquantes..."
echo "Ajout des colonnes manquantes à la table user..."
docker compose exec -e PGPASSWORD=ckan ckan psql -h db -U ckan -d ckan -c "
ALTER TABLE \"user\" ADD COLUMN IF NOT EXISTS last_active TIMESTAMP;
ALTER TABLE \"user\" ADD COLUMN IF NOT EXISTS image_url TEXT;
ALTER TABLE \"user\" ADD COLUMN IF NOT EXISTS plugin_extras TEXT;
" 2>/dev/null || echo "Colonnes user déjà présentes"

echo "Ajout de la colonne permission_labels à la table activity..."
docker compose exec -e PGPASSWORD=ckan ckan psql -h db -U ckan -d ckan -c "
ALTER TABLE activity ADD COLUMN IF NOT EXISTS permission_labels TEXT[];
" 2>/dev/null || echo "Colonne activity déjà présente"

echo "Ajout de la colonne plugin_data à la table package..."
docker compose exec -e PGPASSWORD=ckan ckan psql -h db -U ckan -d ckan -c "
ALTER TABLE package ADD COLUMN IF NOT EXISTS plugin_data TEXT;
" 2>/dev/null || echo "Colonne package déjà présente"

echo "Ajout de la colonne metadata_modified à la table resource..."
docker compose exec -e PGPASSWORD=ckan ckan psql -h db -U ckan -d ckan -c "
ALTER TABLE resource ADD COLUMN IF NOT EXISTS metadata_modified TIMESTAMP;
" 2>/dev/null || echo "Colonne resource déjà présente"

echo "Colonnes manquantes vérifiées et ajoutées si nécessaire"

# Reconstruire l'index de recherche seulement si nécessaire
echo "Vérification de l'index de recherche..."
PACKAGE_COUNT=$(docker compose exec -e PGPASSWORD=ckan ckan psql -h db -U ckan -d ckan -t -c "SELECT COUNT(*) FROM package WHERE state = 'active';" 2>/dev/null | tr -d ' \n' || echo "0")
if [ "$PACKAGE_COUNT" -gt 0 ]; then
    echo "   $PACKAGE_COUNT packages trouvés, reconstruction de l'index..."
    docker compose exec ckan ckan -c /srv/app/ckan.ini search-index rebuild 2>&1 | grep -v "WARNING\|ERROR" || echo "   Index reconstruit"
else
    echo "   Aucun package actif, skip de la reconstruction de l'index"
fi

echo ""
echo "=== CKAN Dataizen est prêt ! ==="
echo ""
# echo "Interface web: http://localhost:8080"
echo "Comptes administrateurs disponibles:"
echo "   - ckan_admin (API Key: ckan-local-dev-apikey)"
echo "   - dataizendev / dataizendev11 (mot de passe: dataizendev11)"
echo "Organisation: dataizen-dev"
echo ""
echo "Services disponibles:"
echo "   - CKAN: http://localhost:8080"
echo "   - PostgreSQL: localhost:5432"
echo "   - Solr: http://localhost:8983"
echo "   - Datapusher: http://localhost:8800"
echo "   - pygeoapi (OGC API): http://localhost:5001"
echo "   - MapServer (WMS/WFS): http://localhost:8081"

echo "Configuration pour Drupal:"
echo "   CKAN_URL=http://localhost:8080/"
echo "   CKAN_API_KEY=ckan-local-dev-apikey"
echo "   CLIENT_ORGANISATION=dataizen-dev"
echo ""
echo "Commandes utiles:"
echo "   - Voir les logs: docker compose logs -f"
echo "   - Arrêter: docker compose down"
echo "   - Redémarrer: docker compose restart"
echo "   - Accéder au container CKAN: docker compose exec ckan bash"
echo "   - Tester l'API: curl -H \"Authorization: ckan-local-dev-apikey\" http://localhost:8080/api/3/action/package_list"
echo ""
# echo " IMPORTANT: Cette instance est configurée pour le développement local"
# echo "   Elle utilise le dump minimal dumps/ckan.dump (96KB) avec l'organisation dataizen-dev"
echo ""

# Synchronisation OGC automatique (sauf si explicitement désactivée)
if [ "$INIT_OGC" = false ]; then
    echo "Synchronisation automatique OGC..."
    echo "====================================="
    
    # Attendre que CKAN soit complètement prêt
    echo "Attente que CKAN soit complètement prêt..."
    sleep 15
    
    # Vérifier si le script de synchronisation existe
    if [ -f "./sync-ogc.sh" ]; then
        echo "Lancement de la synchronisation OGC automatique..."
        ./sync-ogc.sh
        if [ $? -eq 0 ]; then
            echo "OGC synchronisé automatiquement avec succès !"
            echo "Services OGC disponibles sur:"
            echo "   - Interface pygeoapi: http://localhost:5001"
            echo "   - Collections: http://localhost:5001/collections"
            echo "   - MapServer WMS: http://localhost:8081/wms?SERVICE=WMS&REQUEST=GetCapabilities"
            echo ""
            
            # Étape 1: Import des fichiers géospatiaux dans datagis (seulement si --todatagis)
            # IMPORTANT: L'import doit être fait AVANT la génération des mapfiles
            if [ "$TO_DATAGIS" = true ]; then
                echo "Import des fichiers géospatiaux dans datagis..."
                if docker compose ps ckan | grep -q "Up"; then
                    docker compose exec ckan python3 /usr/local/bin/import-geospatial-to-datagis.py 2>/dev/null || echo " Erreur import datagis (peut être normal si aucun fichier géospatial)"
                    echo ""
                fi
            else
                echo "Import vers datagis ignoré (utilisez --todatagis pour importer)"
                echo "   Les mapfiles seront générés depuis les tables datagis existantes"
                echo ""
            fi
            
            # Étape 2: Générer les mapfiles MapServer pour les datasets géospatiaux (toujours, après l'import)
            if docker compose ps mapserver | grep -q "Up"; then
                echo "Génération des mapfiles MapServer..."
                docker compose exec mapserver python3 /usr/local/bin/generate-mapfile.py --use-datagis 2>/dev/null || echo " Erreur génération mapfiles (peut être normal si aucun dataset géospatial)"
                echo ""
            fi
        else
            echo " Erreur lors de la synchronisation OGC automatique"
            echo "Vous pouvez relancer manuellement avec: ./sync-ogc.sh"
            echo ""
        fi
    else
        echo "Script de synchronisation OGC non trouvé (sync-ogc.sh)"
        echo "Assurez-vous d'être dans le bon répertoire"
        echo ""
    fi
fi

# Ajouter un dataset de test si demandé (après que l'API soit prête)
if [ "$ADD_TEST_DATASET" = true ]; then
    echo "Ajout du dataset de test géospatial..."
    docker compose exec ckan /srv/app/add-test-dataset.sh
    if [ $? -eq 0 ]; then
        echo "Dataset de test ajouté avec succès"
        echo "Vous pouvez maintenant tester les services OGC :"
        echo "   - OGC API Features: http://localhost:5001/collections/test-ogc-dataset/items"
        echo "   - MapServer WMS: http://localhost:8081/wms?SERVICE=WMS&REQUEST=GetCapabilities"
        echo "   - MapServer WFS: http://localhost:8081/wfs?SERVICE=WFS&REQUEST=GetCapabilities"
        echo ""
        
        # Générer le mapfile pour le dataset de test
        if docker compose ps mapserver | grep -q "Up"; then
            echo "Génération du mapfile pour le dataset de test..."
            docker compose exec mapserver python3 /usr/local/bin/generate-mapfile.py --dataset test-ogc-dataset 2>/dev/null || echo " Erreur génération mapfile (peut être normal)"
            echo ""
        fi
    else
        echo " Erreur lors de l'ajout du dataset de test"
        echo ""
    fi
fi

# Initialiser OGC si demandé
if [ "$INIT_OGC" = true ]; then
    echo "Initialisation et synchronisation OGC..."
    echo "============================================="
    
    # Vérifier si le script de synchronisation existe
    if [ -f "./sync-ogc.sh" ]; then
        echo "Lancement de la synchronisation OGC..."
        ./sync-ogc.sh
        if [ $? -eq 0 ]; then
            echo "OGC initialisé et synchronisé avec succès !"
            echo "Services OGC disponibles sur:"
            echo "   - Interface pygeoapi: http://localhost:5001"
            echo "   - Collections: http://localhost:5001/collections"
            echo "   - API OpenAPI: http://localhost:5001/openapi"
            echo "   - MapServer WMS: http://localhost:8081/wms?SERVICE=WMS&REQUEST=GetCapabilities"
            echo "   - MapServer WFS: http://localhost:8081/wfs?SERVICE=WFS&REQUEST=GetCapabilities"
            echo ""
            
            # Étape 1: Import des fichiers géospatiaux dans datagis (seulement si --todatagis)
            # IMPORTANT: L'import doit être fait AVANT la génération des mapfiles
            if [ "$TO_DATAGIS" = true ]; then
                echo "Import des fichiers géospatiaux dans datagis..."
                if docker compose ps ckan | grep -q "Up"; then
                    docker compose exec ckan python3 /usr/local/bin/import-geospatial-to-datagis.py 2>/dev/null || echo " Erreur import datagis (peut être normal si aucun fichier géospatial)"
                    echo ""
                fi
            else
                echo "Import vers datagis ignoré (utilisez --todatagis pour importer)"
                echo "   Les mapfiles seront générés depuis les tables datagis existantes"
                echo ""
            fi
            
            # Étape 2: Générer les mapfiles MapServer pour les datasets géospatiaux (toujours, après l'import)
            if docker compose ps mapserver | grep -q "Up"; then
                echo "Génération des mapfiles MapServer..."
                docker compose exec mapserver python3 /usr/local/bin/generate-mapfile.py --use-datagis 2>/dev/null || echo " Erreur génération mapfiles (peut être normal si aucun dataset géospatial)"
                echo ""
            fi
        else
            echo " Erreur lors de l'initialisation OGC"
            echo "Vous pouvez relancer manuellement avec: ./sync-ogc.sh"
            echo ""
        fi
    else
        echo "Script de synchronisation OGC non trouvé (sync-ogc.sh)"
        echo "Assurez-vous d'être dans le bon répertoire"
        echo ""
    fi
fi

# Afficher les logs si demandé
if [ "$SHOW_LOGS" = true ]; then
    echo "Affichage des logs des services (Ctrl+C pour arrêter)..."
    echo "=================================================================="
    docker compose logs -f
fi