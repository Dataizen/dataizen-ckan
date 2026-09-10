#!/bin/bash
# Script pour trouver l'ID d'un dataset à partir de son nom

DATASET_NAME="${1:-}"
SOURCE_URL="${SOURCE_CKAN_URL:-https://trouver.ternum-bfc.fr}"
SOURCE_TOKEN="${SOURCE_CKAN_TOKEN:-}"

if [ -z "$DATASET_NAME" ]; then
    echo "Usage: $0 <dataset-name>"
    echo ""
    echo "Exemples:"
    echo "  $0 mon-dataset"
    echo "  SOURCE_CKAN_URL=https://source.ckan.fr SOURCE_CKAN_TOKEN=xxx $0 mon-dataset"
    exit 1
fi

if [ -z "$SOURCE_TOKEN" ]; then
    echo " SOURCE_CKAN_TOKEN non défini, certaines commandes peuvent échouer"
    echo "   Définir avec: export SOURCE_CKAN_TOKEN=xxx"
    echo ""
fi

echo "Recherche du dataset: '$DATASET_NAME'"
echo ""

# Récupérer le dataset
echo "Récupération du dataset: $DATASET_NAME"
PACKAGE_JSON=$(curl -s -H "Authorization: $SOURCE_TOKEN" \
    "$SOURCE_URL/api/action/package_show?id=$DATASET_NAME" 2>/dev/null)

DATASET_ID=$(echo "$PACKAGE_JSON" | python3 -c "
import sys, json
try:
    data = json.load(sys.stdin)
    if not data.get('success'):
        error_msg = data.get('error', {}).get('message', 'Erreur inconnue') if isinstance(data.get('error'), dict) else str(data.get('error', 'Erreur inconnue'))
        print(f'Erreur: {error_msg}', file=sys.stderr)
        sys.exit(1)
    package = data.get('result', {})
    dataset_id = package.get('id', '')
    dataset_title = package.get('title', 'N/A')
    print(dataset_id)
    print(f'Title: {dataset_title}', file=sys.stderr)
except json.JSONDecodeError as e:
    print(f'Erreur JSON: {e}', file=sys.stderr)
    sys.exit(1)
except Exception as e:
    print(f'Erreur: {e}', file=sys.stderr)
    sys.exit(1)
" 2>&1)

if [ $? -eq 0 ] && [ -n "$DATASET_ID" ] && [ "$DATASET_ID" != "Erreur:"* ]; then
    echo "ID trouvé: $DATASET_ID"
    echo ""
    echo "Pour réessayer uniquement ce dataset:"
    echo "   python import-from-ckan.py --retry-dataset-ids $DATASET_ID ..."
    echo ""
    echo "Ou avec tous les autres paramètres:"
    echo "   python import-from-ckan.py \\"
    echo "     --source-url $SOURCE_URL \\"
    echo "     --source-token \$SOURCE_TOKEN \\"
    echo "     --target-url \$TARGET_URL \\"
    echo "     --target-token \$TARGET_TOKEN \\"
    echo "     --retry-dataset-ids $DATASET_ID"
else
    echo "Dataset non trouvé ou erreur"
    echo ""
    echo "Vérifiez que:"
    echo "   1. Le nom du dataset est exact: '$DATASET_NAME'"
    echo "   2. Vous avez les permissions nécessaires"
    echo "   3. L'URL source est correcte: $SOURCE_URL"
    exit 1
fi


