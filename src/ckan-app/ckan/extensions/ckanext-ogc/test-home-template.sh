#!/bin/bash
# Script de test pour vérifier que le template home est bien chargé

echo "Vérification du template home/index.html..."

TEMPLATE_PATH="ckanext/ogc/templates/home/index.html"
EXTENSION_PATH="/srv/app/src/ckanext-ogc"

if [ -f "$EXTENSION_PATH/$TEMPLATE_PATH" ]; then
    echo "Template trouvé: $EXTENSION_PATH/$TEMPLATE_PATH"
    echo "Contenu (premières lignes):"
    head -5 "$EXTENSION_PATH/$TEMPLATE_PATH"
else
    echo "Template NON trouvé: $EXTENSION_PATH/$TEMPLATE_PATH"
fi

echo ""
echo "Vérification des helpers dans plugin.py..."
if grep -q "get_home_statistics" ckanext/ogc/plugin.py; then
    echo "Helper get_home_statistics trouvé dans plugin.py"
else
    echo "Helper get_home_statistics NON trouvé dans plugin.py"
fi

if grep -q "get_popular_tags" ckanext/ogc/plugin.py; then
    echo "Helper get_popular_tags trouvé dans plugin.py"
else
    echo "Helper get_popular_tags NON trouvé dans plugin.py"
fi

if grep -q "get_categories" ckanext/ogc/plugin.py; then
    echo "Helper get_categories trouvé dans plugin.py"
else
    echo "Helper get_categories NON trouvé dans plugin.py"
fi

echo ""
echo "Vérification des snippets..."
SNIPPETS=("snippets/popular_tags.html" "snippets/home_statistics.html" "snippets/home_categories.html")
for snippet in "${SNIPPETS[@]}"; do
    if [ -f "$EXTENSION_PATH/ckanext/ogc/templates/$snippet" ]; then
        echo "Snippet trouvé: $snippet"
    else
        echo "Snippet NON trouvé: $snippet"
    fi
done
