#!/bin/bash
# Script pour vérifier que le wrapper xloader existe et est exécutable

echo "Vérification du wrapper xloader..."

XLOADER_WRAPPER="/srv/app/xloader_wrapper.py"

# Vérifier si le wrapper existe (créé dans le Dockerfile)
if [ -f "$XLOADER_WRAPPER" ]; then
    echo "Wrapper xloader trouvé: $XLOADER_WRAPPER"
    # Le wrapper est déjà exécutable depuis le Dockerfile, mais on vérifie quand même
    # Utiliser sudo si nécessaire, sinon ignorer l'erreur chmod
    chmod +x "$XLOADER_WRAPPER" 2>/dev/null || sudo chmod +x "$XLOADER_WRAPPER" 2>/dev/null || echo " Impossible de chmod (déjà exécutable ou permissions OK)"
    echo "Wrapper xloader vérifié"
else
    echo " Wrapper xloader non trouvé: $XLOADER_WRAPPER"
    echo " Le wrapper devrait être créé dans le Dockerfile"
    echo " Le worker xloader pourrait ne pas démarrer correctement"
fi

