#!/bin/bash
# Script de diagnostic pour vérifier la configuration du storage CKAN

CKAN_INI="${CKAN_INI:-/srv/app/ckan.ini}"

echo "Diagnostic de la configuration du storage CKAN"
echo "=================================================="
echo ""

# Vérifier la configuration dans ckan.ini
echo "1. Configuration dans ckan.ini:"
STORAGE_PATH=$(ckan config-tool "$CKAN_INI" --get ckan.storage_path 2>/dev/null || echo "NON CONFIGURÉ")
echo "   ckan.storage_path = $STORAGE_PATH"
echo ""

# Vérifier l'existence du répertoire
if [ "$STORAGE_PATH" != "NON CONFIGURÉ" ] && [ -n "$STORAGE_PATH" ]; then
    echo "2. Vérification du répertoire:"
    if [ -d "$STORAGE_PATH" ]; then
        echo "   Répertoire existe: $STORAGE_PATH"
        ls -ld "$STORAGE_PATH" | awk '{print "   Permissions: " $1 " Owner: " $3 ":" $4}'
    else
        echo "    Répertoire n'existe pas: $STORAGE_PATH"
    fi
    
    # Vérifier le répertoire parent
    PARENT_DIR=$(dirname "$STORAGE_PATH")
    echo ""
    echo "3. Vérification du répertoire parent:"
    if [ -d "$PARENT_DIR" ]; then
        echo "   Répertoire parent existe: $PARENT_DIR"
        ls -ld "$PARENT_DIR" | awk '{print "   Permissions: " $1 " Owner: " $3 ":" $4}'
        
        # Vérifier l'accessibilité en écriture
        if [ -w "$PARENT_DIR" ]; then
            echo "   Répertoire parent accessible en écriture"
        else
            echo "    Répertoire parent NON accessible en écriture"
        fi
    else
        echo "   Répertoire parent n'existe pas: $PARENT_DIR"
    fi
fi

echo ""
echo "4. Vérification via Python CKAN:"
python3 << 'PYTHON'
import os
import sys
sys.path.insert(0, '/srv/app/src')

try:
    from ckan.common import config
    storage_path = config.get('ckan.storage_path')
    if storage_path:
        print(f"   Storage path détecté par CKAN: {storage_path}")
        
        # Vérifier si le répertoire existe
        if os.path.exists(storage_path):
            print(f"   Répertoire existe")
            if os.access(storage_path, os.W_OK):
                print(f"   Répertoire accessible en écriture")
            else:
                print(f"    Répertoire NON accessible en écriture")
        else:
            print(f"    Répertoire n'existe pas (sera créé au premier upload)")
            
        # Vérifier le répertoire parent
        parent_dir = os.path.dirname(storage_path)
        if os.path.exists(parent_dir):
            print(f"   Répertoire parent existe: {parent_dir}")
            if os.access(parent_dir, os.W_OK):
                print(f"   Répertoire parent accessible en écriture")
            else:
                print(f"    Répertoire parent NON accessible en écriture")
        else:
            print(f"   Répertoire parent n'existe pas: {parent_dir}")
    else:
        print("   Storage path NON configuré dans CKAN")
except Exception as e:
    print(f"   Erreur lors de la vérification: {e}")
PYTHON

echo ""
echo "=================================================="
echo "Diagnostic terminé"


