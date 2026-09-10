#!/bin/bash
set -e

echo "Configuration du logging pour filtrer les 404 de datastore_search (conservation des autres logs INFO)..."

# Définir CKAN_INI si non défini
CKAN_INI="${CKAN_INI:-/srv/app/ckan.ini}"

# Le filtre sera appliqué automatiquement par le plugin OGC
# On garde le logger au niveau INFO pour conserver les logs utiles
# Le filtre DatastoreSearch404Filter filtrera uniquement les 404 de datastore_search

# Supprimer l'ancienne configuration si elle existe (niveau ERROR ou WARNING)
if grep -q "^\[logger_ckan\.config\.middleware\.flask_app\]" "$CKAN_INI" 2>/dev/null; then
    echo "Suppression de l'ancienne configuration flask_app..."
    # Supprimer la section complète
    sed -i '/^\[logger_ckan\.config\.middleware\.flask_app\]/,/^\[/ { /^\[logger_ckan\.config\.middleware\.flask_app\]/d; /^qualname = ckan\.config\.middleware\.flask_app/d; /^handlers =/d; /^level =/d; }' "$CKAN_INI" 2>/dev/null || true
    # Nettoyer les lignes vides en trop
    sed -i '/^$/N;/^\n$/d' "$CKAN_INI" 2>/dev/null || true
fi

# Ne pas configurer le logger flask_app au niveau ERROR
# Le filtre personnalisé dans le plugin OGC s'occupera de filtrer les 404 de datastore_search
# tout en conservant les autres logs INFO utiles

echo "Configuration du logging flask_app terminée"
echo "Le filtre DatastoreSearch404Filter filtrera uniquement les 404 de /api/action/datastore_search"
echo "Les autres logs INFO (import, mapfiles, etc.) seront conservés"

