#!/bin/bash
# Script pour builder et pusher les images pour la production (amd64)
# Utilise automatiquement buildx pour compiler pour amd64 depuis macOS ARM

set -e

# Activer buildx
export DOCKER_BUILDKIT=1
export COMPOSE_DOCKER_CLI_BUILD=1

# Détecter si on est sur macOS ARM
if [[ "$(uname)" == "Darwin" ]] && ([[ "$(uname -m)" == "arm64" ]] || [[ "$(uname -m)" == "aarch64" ]]); then
    echo "macOS ARM détecté, configuration de buildx pour amd64..."
    
    # Vérifier si buildx est disponible
    if ! docker buildx version &> /dev/null; then
        echo "Docker buildx n'est pas disponible. Veuillez mettre à jour Docker Desktop."
        exit 1
    fi
    
    # Créer ou utiliser le builder amd64
    if ! docker buildx ls | grep -q "amd64-builder"; then
        echo "Création du builder amd64-builder..."
        docker buildx create --name amd64-builder --driver docker-container --platform linux/amd64 --use
        docker buildx inspect --bootstrap
    else
        echo "Builder amd64-builder trouvé"
        docker buildx use amd64-builder 2>/dev/null || true
        docker buildx inspect --bootstrap 2>/dev/null || true
    fi
fi

# Déterminer quelle action effectuer
ACTION="${1:-build}"

case "$ACTION" in
    build)
        SERVICE="${2:-ckan}"
        echo "Construction de l'image $SERVICE pour amd64..."
        docker compose build --platform linux/amd64 "$SERVICE"
        ;;
    push)
        SERVICE="${2:-ckan}"
        echo "Push de l'image $SERVICE..."
        docker compose push "$SERVICE"
        ;;
    build-push)
        SERVICE="${2:-ckan}"
        echo "Construction et push de l'image $SERVICE pour amd64..."
        docker compose build --platform linux/amd64 "$SERVICE"
        docker compose push "$SERVICE"
        ;;
    *)
        echo "Usage: $0 [build|push|build-push] [service]"
        echo ""
        echo "Exemples:"
        echo "  $0 build ckan          # Builder l'image ckan pour amd64"
        echo "  $0 push ckan           # Pusher l'image ckan"
        echo "  $0 build-push ckan     # Builder et pusher l'image ckan"
        echo "  $0 build-push mapserver # Builder et pusher l'image mapserver"
        exit 1
        ;;
esac

echo "Opération terminée"











