#!/bin/bash
# Script pour configurer Docker buildx pour compiler pour amd64 depuis macOS ARM

set -e

echo "Configuration de Docker buildx pour amd64..."

# Vérifier si on est sur macOS ARM
if [[ "$(uname)" != "Darwin" ]]; then
    echo "Ce script est conçu pour macOS. Sur Linux, buildx devrait fonctionner nativement."
fi

# Vérifier si Docker est installé
if ! command -v docker &> /dev/null; then
    echo "Docker n'est pas installé"
    exit 1
fi

# Activer buildx
export DOCKER_BUILDKIT=1

# Créer un builder pour amd64 s'il n'existe pas
if ! docker buildx ls | grep -q "amd64-builder"; then
    echo "Création du builder amd64-builder..."
    docker buildx create --name amd64-builder --driver docker-container --platform linux/amd64 --use
    echo "Builder amd64-builder créé"
else
    echo "Builder amd64-builder existe déjà"
fi

# Utiliser le builder
echo "Activation du builder amd64-builder..."
docker buildx use amd64-builder

# Inspecter et bootstrapper le builder
echo "Inspection du builder..."
docker buildx inspect --bootstrap

echo ""
echo "Configuration terminée !"
echo ""
echo "Vous pouvez maintenant compiler pour amd64 avec :"
echo "  docker buildx build --platform linux/amd64 -t votre-image ."
echo ""
echo "Ou utiliser docker compose qui utilisera automatiquement buildx :"
echo "  docker compose build --platform linux/amd64"











