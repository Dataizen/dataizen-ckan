#!/bin/bash
# Script informatif : le patch d'ajout du header Authorization n'est plus nécessaire
# 
# La route download autorise maintenant les appels internes avec ignore_auth=True
# grâce à la modification de get_auth_functions dans ckanext-dataload-router
#
# Ce script vérifie si un patch précédent existe et informe qu'il n'est plus nécessaire

echo "Vérification du patch xloader auth header..."

XLOADER_JOBS_FILE="/srv/app/src/ckanext-xloader/ckanext/xloader/jobs.py"

# Vérifier rapidement si le fichier existe
if [ ! -f "$XLOADER_JOBS_FILE" ]; then
    echo " Fichier xloader/jobs.py non trouvé"
    echo "Configuration xloader auth header terminée (skip)"
    exit 0
fi

# Vérifier si un patch précédent existe
if grep -q "# Patched by databfc: add Authorization header" "$XLOADER_JOBS_FILE" 2>/dev/null; then
    echo " Un patch précédent a été détecté dans jobs.py"
    echo "   Ce patch n'est plus nécessaire car la route download autorise maintenant"
    echo "   les appels internes avec ignore_auth=True (via ckanext-dataload-router)"
    echo "   Le patch existant ne causera pas de problème, mais il peut être retiré si souhaité"
else
    echo "Aucun patch précédent détecté"
    echo "   Le patch n'est pas nécessaire : la route download autorise les appels internes"
fi

echo "Configuration xloader auth header terminée"
echo "   Les téléchargements internes (xloader) sont autorisés via ignore_auth"
