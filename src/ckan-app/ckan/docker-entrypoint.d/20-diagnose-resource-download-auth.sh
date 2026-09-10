#!/bin/bash
# Script de diagnostic pour identifier où la protection des téléchargements est appliquée

echo "Diagnostic: Identification de la protection des téléchargements de ressources..."

CKAN_INI="${CKAN_INI:-/srv/app/ckan.ini}"

echo ""
echo "=== 1. Vérification de la configuration CKAN ==="
echo ""

# Vérifier ckan.auth.resource_download
echo "Configuration ckan.auth.resource_download:"
if ckan config-tool "$CKAN_INI" 2>/dev/null | grep -E "^ckan\.auth\.resource_download" || true; then
    echo "   Configuration trouvée - peut bloquer les téléchargements publics"
else
    echo "  Aucune restriction configurée (par défaut, les ressources publiques sont téléchargeables)"
fi

echo ""
echo "=== 2. Vérification des extensions qui interceptent resource_download ==="
echo ""

# Chercher les extensions qui implémentent IAuthFunctions
echo "Extensions qui implémentent IAuthFunctions:"
python3 << 'PYTHON_CHECK' 2>/dev/null || echo "   Impossible de vérifier les extensions"
import sys
import os

# Chercher dans les extensions installées
extensions_paths = [
    '/srv/app/src/ckanext-*/ckanext/*/plugin.py',
    '/srv/app/ckanext-*/ckanext/*/plugin.py',
    '/usr/lib/ckan/default/src/ckanext-*/ckanext/*/plugin.py',
]

import glob
import re

found_extensions = []

for pattern in extensions_paths:
    for path in glob.glob(pattern):
        try:
            with open(path, 'r') as f:
                content = f.read()
                # Chercher IAuthFunctions
                if 'IAuthFunctions' in content:
                    # Chercher resource_download dans get_auth_functions
                    if 'resource_download' in content:
                        found_extensions.append(path)
                        print(f"   {path} intercepte resource_download")
        except:
            pass

if not found_extensions:
    print("  Aucune extension trouvée qui intercepte resource_download")

PYTHON_CHECK

echo ""
echo "=== 3. Vérification de Keycloak (peut bloquer les requêtes) ==="
echo ""

# Vérifier si Keycloak est activé
if ckan config-tool "$CKAN_INI" 2>/dev/null | grep -q "ckan.plugins.*keycloak"; then
    echo "   Extension Keycloak activée - peut bloquer les requêtes sans authentification"
    echo "  Vérifier la configuration Keycloak pour les ressources publiques"
else
    echo "  Extension Keycloak non activée"
fi

echo ""
echo "=== 4. Vérification des middlewares/proxies ==="
echo ""

echo "  Vérifier manuellement:"
echo "     - Configuration nginx/ingress pour les restrictions"
echo "     - Headers requis (Authorization, etc.)"
echo "     - IP whitelisting"

echo ""
echo "=== 5. Test de téléchargement (si possible) ==="
echo ""

# Essayer de trouver une ressource publique pour tester
echo "  Recherche d'une ressource publique pour test..."
python3 << 'PYTHON_TEST' 2>/dev/null || echo "   Impossible de tester (CKAN non disponible ou erreur)"
import os
import sys

try:
    # Importer CKAN
    sys.path.insert(0, '/srv/app/src/ckan')
    from ckan import model
    from ckan.logic import get_action
    
    # Créer un contexte avec ignore_auth pour trouver une ressource publique
    context = {'model': model, 'session': model.Session, 'ignore_auth': True}
    
    # Chercher un dataset public avec une ressource
    try:
        datasets = get_action('package_list')(context, {})
        if datasets:
            # Prendre le premier dataset
            dataset_name = datasets[0]
            dataset = get_action('package_show')(context, {'id': dataset_name})
            
            if dataset.get('resources'):
                resource = dataset['resources'][0]
                resource_id = resource['id']
                resource_url = f"http://localhost:5000/dataset/{dataset_name}/resource/{resource_id}/download"
                
                print(f"  Ressource de test trouvée:")
                print(f"     Dataset: {dataset_name}")
                print(f"     Resource ID: {resource_id}")
                print(f"     URL: {resource_url}")
                print(f"     Private: {dataset.get('private', False)}")
                print("")
                print("  Test sans authentification:")
                import subprocess
                result = subprocess.run(
                    ['curl', '-s', '-o', '/dev/null', '-w', '%{http_code}', resource_url],
                    capture_output=True,
                    text=True,
                    timeout=5
                )
                http_code = result.stdout.strip()
                if http_code == '200':
                    print(f"     HTTP {http_code} - Téléchargement public autorisé")
                elif http_code == '403':
                    print(f"     HTTP {http_code} - Téléchargement bloqué (403 Forbidden)")
                    print("     Le problème vient probablement de CKAN ou d'un middleware")
                else:
                    print(f"      HTTP {http_code} - Code inattendu")
                
                print("")
                print("  Test avec authentification:")
                api_key = os.environ.get('CKAN_API_KEY', '')
                if api_key:
                    result = subprocess.run(
                        ['curl', '-s', '-o', '/dev/null', '-w', '%{http_code}', 
                         '-H', f'Authorization: {api_key}', resource_url],
                        capture_output=True,
                        text=True,
                        timeout=5
                    )
                    http_code = result.stdout.strip()
                    if http_code == '200':
                        print(f"     HTTP {http_code} - Téléchargement autorisé avec auth")
                        print("     Conclusion: Le téléchargement nécessite une authentification")
                    else:
                        print(f"      HTTP {http_code} - Même avec auth, problème persiste")
                else:
                    print("      CKAN_API_KEY non défini, impossible de tester avec auth")
            else:
                print("   Aucune ressource trouvée dans les datasets")
        else:
            print("   Aucun dataset trouvé")
    except Exception as e:
        print(f"   Erreur lors de la recherche: {e}")
except Exception as e:
    print(f"   Impossible d'importer CKAN: {e}")

PYTHON_TEST

echo ""
echo "  Pour tester manuellement:"
echo "     curl -v http://localhost:5000/dataset/<dataset>/resource/<resource_id>/download"
echo "     curl -v -H 'Authorization: <api_key>' http://localhost:5000/dataset/<dataset>/resource/<resource_id>/download"

echo ""
echo "=== 6. Recommandations ==="
echo ""

echo "  Si le 403 vient de CKAN:"
echo "     - Vérifier ckan.auth.resource_download dans ckan.ini"
echo "     - Vérifier les extensions qui interceptent resource_download"
echo ""
echo "  Si le 403 vient d'un proxy/middleware:"
echo "     - Vérifier la configuration nginx/ingress"
echo "     - Vérifier les règles de sécurité (WAF, etc.)"
echo ""
echo "  Solution temporaire (si nécessaire):"
echo "     - Utiliser le patch 18-patch-xloader-auth-header.sh pour ajouter l'auth dans xloader"
echo "     - OU corriger la configuration/proxy pour permettre les téléchargements publics"

echo ""
echo "Diagnostic terminé"

