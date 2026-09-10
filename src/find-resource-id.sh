#!/bin/bash
# Script pour trouver l'ID d'une ressource à partir de son nom et du dataset

RESOURCE_NAME="${1:-Scan Express 25 Classique département 058}"
DATASET_NAME="${2:-}"
SOURCE_URL="${SOURCE_CKAN_URL:-https://trouver.ternum-bfc.fr}"
SOURCE_TOKEN="${SOURCE_CKAN_TOKEN:-}"

if [ -z "$SOURCE_TOKEN" ]; then
    echo "SOURCE_CKAN_TOKEN requis"
    echo "Usage: SOURCE_CKAN_TOKEN=xxx $0 [resource-name] [dataset-name]"
    exit 1
fi

echo "Recherche de la ressource: '$RESOURCE_NAME'"
if [ -n "$DATASET_NAME" ]; then
    echo "   Dans le dataset: '$DATASET_NAME'"
fi
echo ""

# Si le dataset est fourni, chercher directement dedans
if [ -n "$DATASET_NAME" ]; then
    echo "Récupération du dataset: $DATASET_NAME"
    PACKAGE_JSON=$(curl -s -H "Authorization: $SOURCE_TOKEN" \
        "$SOURCE_URL/api/action/package_show?id=$DATASET_NAME")
    
    RESOURCE_ID=$(echo "$PACKAGE_JSON" | python3 -c "
import sys, json
try:
    data = json.load(sys.stdin)
    if not data.get('success'):
        print('', end='')
        sys.exit(1)
    package = data.get('result', {})
    resources = package.get('resources', [])
    for res in resources:
        if res.get('name', '').strip() == '$RESOURCE_NAME':
            print(res.get('id', ''))
            break
except:
    pass
")
    
    if [ -n "$RESOURCE_ID" ]; then
        echo "ID trouvé: $RESOURCE_ID"
        echo ""
        echo "Pour réessayer uniquement cette ressource:"
        echo "   python import-from-ckan.py --retry-resource-ids $RESOURCE_ID ..."
        exit 0
    fi
fi

# Sinon, chercher dans tous les datasets (plus lent)
echo "Recherche dans tous les datasets (cela peut prendre du temps)..."
echo ""

# Utiliser package_search pour trouver les datasets contenant cette ressource
SEARCH_RESULT=$(curl -s -H "Authorization: $SOURCE_TOKEN" \
    "$SOURCE_URL/api/action/package_search?q=*&rows=1000")

RESOURCE_ID=$(echo "$SEARCH_RESULT" | python3 -c "
import sys, json
try:
    data = json.load(sys.stdin)
    if not data.get('success'):
        print('', end='')
        sys.exit(1)
    results = data.get('result', {}).get('results', [])
    for package in results:
        resources = package.get('resources', [])
        for res in resources:
            if res.get('name', '').strip() == '$RESOURCE_NAME':
                print(res.get('id', ''))
                print(f\"Dataset: {package.get('name', 'N/A')} ({package.get('title', 'N/A')})\", file=sys.stderr)
                sys.exit(0)
except Exception as e:
    print(f'Erreur: {e}', file=sys.stderr)
    pass
")

if [ -n "$RESOURCE_ID" ]; then
    echo "ID trouvé: $RESOURCE_ID"
    echo ""
    echo "Pour réessayer uniquement cette ressource:"
    echo "   python import-from-ckan.py --retry-resource-ids $RESOURCE_ID ..."
else
    echo "Ressource non trouvée"
    echo ""
    echo "Vérifiez que:"
    echo "   1. Le nom de la ressource est exact: '$RESOURCE_NAME'"
    echo "   2. Le dataset est correct (si fourni): '$DATASET_NAME'"
    echo "   3. Vous avez les permissions nécessaires"
fi


