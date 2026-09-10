#!/bin/bash
# Script pour s'assurer que les téléchargements de ressources publiques sont autorisés sans authentification
# 
# Problème: CKAN (ou un middleware/proxy) bloque les téléchargements sans header d'authentification
# Solution: S'assurer que les ressources publiques peuvent être téléchargées sans auth
#
# Causes possibles:
# 1. Config CKAN: ckan.auth.resource_download = logged_in_users_only
# 2. Extension qui durcit resource_download
# 3. Proxy (nginx/ingress) qui bloque sans auth
# 4. Patch maison qui modifie le comportement

echo "Configuration pour permettre les téléchargements publics de ressources..."

CKAN_INI="${CKAN_INI:-/srv/app/ckan.ini}"

# Par défaut, CKAN permet les téléchargements publics pour les ressources publiques
# Mais certaines configurations ou extensions peuvent restreindre cela
# On s'assure que les téléchargements publics sont autorisés

# Vérifier et configurer ckan.auth pour permettre les téléchargements publics
# Note: Par défaut, CKAN permet les téléchargements publics, mais on s'assure que c'est bien configuré

# Si une extension ou une config bloque les téléchargements, on peut utiliser:
# ckan.auth.resource_download = logged_in_users_only (bloque les téléchargements publics)
# Par défaut, cette option n'est pas définie, ce qui permet les téléchargements publics

# Vérifier si une restriction existe et la retirer si nécessaire
echo "Vérification des restrictions sur les téléchargements..."
if ckan config-tool "$CKAN_INI" 2>/dev/null | grep -q "ckan.auth.resource_download.*logged_in_users_only"; then
    echo " Restriction trouvée sur les téléchargements, suppression..."
    # Retirer la ligne si elle existe (utiliser sed pour être sûr)
    sed -i '/^ckan\.auth\.resource_download.*logged_in_users_only/d' "$CKAN_INI" 2>/dev/null || true
    echo "Restriction supprimée"
elif ckan config-tool "$CKAN_INI" 2>/dev/null | grep -q "ckan.auth.resource_download"; then
    echo "Configuration resource_download trouvée (vérifier manuellement si nécessaire)"
    ckan config-tool "$CKAN_INI" 2>/dev/null | grep "ckan.auth.resource_download" || true
else
    echo "Aucune restriction sur les téléchargements publics trouvée"
fi

# S'assurer que les ressources publiques peuvent être téléchargées sans authentification
# En CKAN, par défaut, les ressources publiques sont téléchargeables sans auth
# On vérifie juste qu'aucune config ne bloque cela

# Note: Si le problème persiste, vérifier:
# 1. Les logs CKAN pour voir si c'est CKAN qui bloque (403) ou un proxy
# 2. La configuration nginx/ingress pour voir s'il y a des restrictions
# 3. Les extensions qui pourraient intercepter resource_download

echo "Configuration des téléchargements publics terminée"
echo "Les ressources publiques devraient être téléchargeables sans authentification"
echo "Si le problème persiste, vérifier:"
echo "    - Les logs CKAN pour identifier la source du 403"
echo "    - La configuration nginx/ingress pour les restrictions"
echo "    - Les extensions qui pourraient intercepter resource_download"

