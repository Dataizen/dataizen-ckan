#!/bin/bash
# Script pour redémarrer pygeoapi depuis le container CKAN
# Supporte Docker local et Kubernetes

set -e

CONTAINER_NAME="${PYGEOAPI_CONTAINER_NAME:-pygeoapi}"
NAMESPACE="${KUBERNETES_NAMESPACE:-default}"
DEPLOYMENT_NAME="${PYGEOAPI_DEPLOYMENT_NAME:-pygeoapi}"

# Détecter l'environnement: Kubernetes ou Docker
is_kubernetes() {
    # Vérifier si on est dans Kubernetes (présence de variables d'environnement Kubernetes)
    [ -n "${KUBERNETES_SERVICE_HOST:-}" ] || \
    [ -n "${KUBERNETES_SERVICE_PORT:-}" ] || \
    [ -f /var/run/secrets/kubernetes.io/serviceaccount/token ] || \
    [ -d /var/run/secrets/kubernetes.io/serviceaccount ]
}

# Redémarrer dans Kubernetes
restart_kubernetes() {
    echo "Redémarrage de pygeoapi dans Kubernetes..."
    
    # Méthode 1: Utiliser l'API Kubernetes directement (prioritaire: fonctionne depuis un pod sans kubectl)
    if [ -f /var/run/secrets/kubernetes.io/serviceaccount/token ]; then
        echo "   Utilisation de l'API Kubernetes..."
        TOKEN=$(cat /var/run/secrets/kubernetes.io/serviceaccount/token)
        CA_CERT="/var/run/secrets/kubernetes.io/serviceaccount/ca.crt"
        API_SERVER="https://${KUBERNETES_SERVICE_HOST}:${KUBERNETES_SERVICE_PORT}"
        
        # Annoter le deployment pour forcer un rollout
        ANNOTATION_VALUE="restarted-at-$(date +%s)"
        curl -s -X PATCH \
            --cacert "$CA_CERT" \
            -H "Authorization: Bearer $TOKEN" \
            -H "Content-Type: application/strategic-merge-patch+json" \
            -d "{\"spec\":{\"template\":{\"metadata\":{\"annotations\":{\"ckan/restarted\":\"${ANNOTATION_VALUE}\"}}}}}" \
            "${API_SERVER}/apis/apps/v1/namespaces/${NAMESPACE}/deployments/${DEPLOYMENT_NAME}" > /dev/null 2>&1
        
        if [ $? -eq 0 ]; then
            echo "Redémarrage du deployment ${DEPLOYMENT_NAME} déclenché via API Kubernetes"
            exit 0
        fi
    fi
    
    # Méthode 2: Utiliser kubectl si disponible (ex: exécution hors cluster)
    if command -v kubectl &> /dev/null; then
        echo "   Utilisation de kubectl rollout restart..."
        if kubectl rollout restart deployment/"${DEPLOYMENT_NAME}" -n "${NAMESPACE}" 2>/dev/null; then
            echo "Commande kubectl envoyée avec succès"
            echo "Attente du redémarrage du pod..."
            kubectl rollout status deployment/"${DEPLOYMENT_NAME}" -n "${NAMESPACE}" --timeout=60s 2>/dev/null || true
            exit 0
        fi
    fi
    
    # Méthode 3: Logger seulement (dans Kubernetes, le redémarrage peut être géré par un opérateur externe)
    echo " Redémarrage automatique non disponible dans cet environnement Kubernetes"
    echo "Redémarrez manuellement le deployment pygeoapi:"
    echo "   kubectl rollout restart deployment/${DEPLOYMENT_NAME} -n ${NAMESPACE}"
    echo "   ou"
    echo "   kubectl delete pod -l app=pygeoapi -n ${NAMESPACE}"
    exit 0  # Ne pas échouer, juste logger
}

# Redémarrer dans Docker
restart_docker() {
    echo "Redémarrage de pygeoapi dans Docker..."
    
    # Méthode 1: Utiliser docker directement si disponible
    if command -v docker &> /dev/null; then
        echo "   Utilisation de docker CLI..."
        if docker restart "$CONTAINER_NAME" 2>/dev/null; then
            echo "pygeoapi redémarré avec succès"
            exit 0
        fi
    fi
    
    # Méthode 2: Utiliser docker compose si disponible
    if command -v docker-compose &> /dev/null || command -v docker &> /dev/null; then
        echo "   Tentative avec docker compose..."
        # Essayer docker compose (v2)
        if docker compose restart pygeoapi 2>/dev/null; then
            echo "pygeoapi redémarré avec succès (docker compose)"
            exit 0
        fi
        # Essayer docker-compose (v1)
        if docker-compose restart pygeoapi 2>/dev/null; then
            echo "pygeoapi redémarré avec succès (docker-compose)"
            exit 0
        fi
    fi
    
    # Méthode 3: Utiliser l'API Docker via curl
    if [ -S /var/run/docker.sock ]; then
        echo "   Utilisation de l'API Docker..."
        
        # Trouver le container ID
        CONTAINER_ID=$(curl -s --unix-socket /var/run/docker.sock \
            "http://localhost/containers/json?filters=%7B%22name%22%3A%5B%22${CONTAINER_NAME}%22%5D%7D" | \
            python3 -c "import sys, json; containers=json.load(sys.stdin); print(containers[0]['Id'] if containers else '')" 2>/dev/null || echo "")
        
        if [ -n "$CONTAINER_ID" ]; then
            # Redémarrer le container
            if curl -s -X POST --unix-socket /var/run/docker.sock \
                "http://localhost/containers/${CONTAINER_ID}/restart" > /dev/null; then
                echo "pygeoapi redémarré avec succès (API Docker)"
                exit 0
            fi
        fi
    fi
    
    # Si aucune méthode n'a fonctionné
    echo "Impossible de redémarrer pygeoapi dans Docker"
    echo "Vérifiez que:"
    echo "   1. Le socket Docker est monté: /var/run/docker.sock"
    echo "   2. Le container pygeoapi existe"
    echo "   3. docker CLI est installé OU curl/python3 sont disponibles"
    exit 1
}

# Détecter l'environnement et redémarrer
if is_kubernetes; then
    restart_kubernetes
else
    restart_docker
fi




