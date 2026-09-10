#!/bin/bash
set -e

echo "Configuration de PYTHONUNBUFFERED pour éviter les erreurs d'écriture..."

# Désactiver le buffering Python pour stdout/stderr
# Cela permet d'écrire immédiatement les logs sans attendre que le buffer soit plein
export PYTHONUNBUFFERED=1

# Vérifier que la variable est bien définie
if [ -z "$PYTHONUNBUFFERED" ]; then
    echo " PYTHONUNBUFFERED n'est pas défini"
    exit 1
fi

echo "PYTHONUNBUFFERED=${PYTHONUNBUFFERED} configuré"
