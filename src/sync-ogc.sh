#!/bin/bash

# ============================================================================
# Script de synchronisation OGC pour Dataizen CKAN
# Usage: ./sync-ogc.sh [--force] [--no-restart]
# ============================================================================

set -e

# Configuration
FORCE_RESTART=false
NO_RESTART=false

# Parse arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        --force)
            FORCE_RESTART=true
            shift
            ;;
        --no-restart)
            NO_RESTART=true
            shift
            ;;
        --help|-h)
            echo "Usage: $0 [--force] [--no-restart] [--help]"
            echo ""
            echo "Options:"
            echo "  --force      Force restart of pygeoapi even if it's running"
            echo "  --no-restart Skip pygeoapi restart (only sync configuration)"
            echo "  --help       Show this help message"
            exit 0
            ;;
        *)
            echo "Unknown option: $1"
            echo "Use --help for usage information"
            exit 1
            ;;
    esac
done

echo "Synchronisation OGC Dataizen CKAN"
echo "===================================="
echo ""

# Vérifier si Docker est en cours d'exécution
if ! docker compose ps ckan > /dev/null 2>&1; then
    echo "Le conteneur CKAN n'est pas en cours d'exécution"
    echo "Démarrez avec: ./start-local.sh"
    exit 1
fi

echo "Conteneur CKAN détecté"

# Vérifier si pygeoapi est accessible
echo "Vérification de pygeoapi..."
if docker compose exec pygeoapi curl -s -f http://localhost:5001/collections > /dev/null 2>&1; then
    echo "pygeoapi est accessible"
    if [ "$FORCE_RESTART" = true ]; then
        echo "Redémarrage forcé de pygeoapi..."
        docker compose restart pygeoapi
        sleep 10
    fi
else
    echo "pygeoapi n'est pas accessible"
    if [ "$NO_RESTART" = false ]; then
        echo "Démarrage du service pygeoapi..."
        docker compose up -d pygeoapi
        sleep 10
        
        if docker compose exec pygeoapi curl -s -f http://localhost:5001/collections > /dev/null 2>&1; then
            echo "pygeoapi démarré avec succès"
        else
            echo "Impossible de démarrer pygeoapi"
            exit 1
        fi
    else
        echo "pygeoapi non accessible et --no-restart activé"
        exit 1
    fi
fi

# Lancer la synchronisation
echo ""
echo "Démarrage de la synchronisation..."
echo ""

# Wait for CKAN to be ready
echo "Attente que CKAN soit prêt..."
for i in {1..30}; do
    if docker compose exec ckan curl -s -f http://localhost:5000/api/action/package_list > /dev/null 2>&1; then
        echo "CKAN est prêt"
        break
    fi
    if [ $i -eq 30 ]; then
        echo "CKAN n'est pas prêt après 30 tentatives"
        echo "Essayez: docker compose logs ckan"
        exit 1
    fi
    sleep 2
done

# La synchronisation doit s'exécuter dans le container pygeoapi car il a le volume en écriture
# Mais elle doit accéder à CKAN via le réseau Docker
docker compose exec pygeoapi bash -c "
cd /srv/app/pygeoapi-providers/ckan_provider
python3 -c \"
import sys
import os
sys.path.append('/srv/app/pygeoapi-providers/ckan_provider')
from ckan_sync import CKANSync

# Utiliser l'URL interne CKAN depuis le réseau Docker
ckan_url = os.getenv('CKAN_URL', 'http://ckan:5000')
api_key = os.getenv('CKAN_API_KEY', 'ckan-local-dev-apikey')
config_path = '/srv/app/pygeoapi/local.config.yml'

print('Initialisation du CKANSync...')
print(f'CKAN URL: {ckan_url}')
print(f'Config path: {config_path}')
sync_tool = CKANSync(ckan_url, api_key, config_path)

print('Découverte des datasets géospatiaux...')
success = sync_tool.sync()

if success:
    print('Synchronisation terminée avec succès')
    print('Collections OGC mises à jour')
    print('Redémarrage de pygeoapi pour prendre en compte la nouvelle configuration...')
else:
    print('Échec de la synchronisation')
    sys.exit(1)
\"
"

# Redémarrer pygeoapi pour prendre en compte la nouvelle configuration
if [ "$NO_RESTART" = false ]; then
    echo ""
    echo "Redémarrage de pygeoapi pour charger la nouvelle configuration..."

    # Redémarrer le service pygeoapi via docker compose
    echo "Redémarrage du service pygeoapi..."
    docker compose restart pygeoapi
    
    # Attendre que pygeoapi redémarre
    echo "Attente du redémarrage de pygeoapi..."
    sleep 10

    # Vérifier que pygeoapi est bien redémarré
    echo "Vérification du redémarrage de pygeoapi..."
    MAX_RETRIES=10
    RETRY_COUNT=0
    while [ $RETRY_COUNT -lt $MAX_RETRIES ]; do
        if docker compose exec pygeoapi curl -s -f http://localhost:5001/collections > /dev/null 2>&1; then
            echo "pygeoapi redémarré avec succès"
            break
        fi
        RETRY_COUNT=$((RETRY_COUNT + 1))
        echo "   Tentative $RETRY_COUNT/$MAX_RETRIES..."
        sleep 2
    done
    
    if [ $RETRY_COUNT -eq $MAX_RETRIES ]; then
        echo "Problème au redémarrage de pygeoapi après $MAX_RETRIES tentatives"
        echo "Vérifiez les logs avec: docker compose logs pygeoapi"
        exit 1
    fi
else
    echo ""
    echo " Redémarrage de pygeoapi ignoré (--no-restart activé)"
    echo "Redémarrez manuellement pygeoapi pour voir les nouvelles collections:"
    echo "   docker compose restart pygeoapi"
fi

if [ $? -eq 0 ]; then
    echo ""
    echo "Synchronisation OGC terminée avec succès !"
    echo ""
    echo "Collections disponibles sur:"
    echo "   - Interface web: http://localhost:5001"
    echo "   - Collections: http://localhost:5001/collections"
    echo ""
    echo "Collections synchronisées:"
    docker compose exec pygeoapi curl -s http://localhost:5001/collections | python3 -m json.tool | grep -E '"id"|"title"' || echo "Aucune collection trouvée"
else
    echo ""
    echo "Échec de la synchronisation OGC"
    exit 1
fi
