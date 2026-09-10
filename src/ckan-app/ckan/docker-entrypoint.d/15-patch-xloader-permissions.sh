#!/bin/bash
# Script non-bloquant pour patcher xloader
# Si le patch échoue, on continue quand même

echo "Patching xloader to use ignore_auth for reading resources..."

XLOADER_JOBS_FILE="/srv/app/src/ckanext-xloader/ckanext/xloader/jobs.py"

# Vérifier rapidement si le fichier existe et si le patch est déjà appliqué
if [ ! -f "$XLOADER_JOBS_FILE" ]; then
    echo " Fichier xloader/jobs.py non trouvé, skip du patch"
    echo "Configuration xloader permissions terminée (skip)"
    exit 0
fi

# Essayer d'appliquer le patch avec un timeout pour éviter de bloquer
echo "Tentative d'application du patch xloader jobs.py (timeout 5s)..."

timeout 5 python3 << 'PYTHON_PATCH' 2>&1 || echo " Patch non appliqué (timeout ou erreur), continuons..."
import sys
import re

file_path = "/srv/app/src/ckanext-xloader/ckanext/xloader/jobs.py"

try:
    with open(file_path, 'r') as f:
        content = f.read()
    
    # Vérifier si le patch est déjà appliqué (vérifier les deux conditions)
    if "# Patched by databfc" in content and "context = {'ignore_auth': True}" in content and "context['user']" in content:
        print("Patch déjà appliqué")
        sys.exit(0)
    
    # Pattern simple pour remplacer context = None
    if "context = None" in content and "def get_resource_and_dataset" in content:
        # Remplacer context = None par context = {'ignore_auth': True}
        # Et corriger le bug où context est remplacé au lieu d'être mis à jour
        new_content = content.replace(
            "    context = None",
            "    # Patched by databfc: use ignore_auth to allow xloader to read resources in private datasets\n    context = {'ignore_auth': True}"
        )
        # Corriger le bug où context est remplacé au lieu d'être mis à jour
        new_content = new_content.replace(
            "    if user is not None:\n        context = {'user': user.name}",
            "    if user is not None:\n        context['user'] = user.name"
        )
        
        # Note: _submit_to_xloader est dans plugin.py, pas jobs.py
        # On ne patche que get_resource_and_dataset ici
        
        if new_content != content:
            # Essayer d'écrire avec sudo
            import subprocess
            import tempfile
            import os
            
            with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.py') as tmp_file:
                tmp_file.write(new_content)
                tmp_path = tmp_file.name
            
            # Copier avec sudo
            result = subprocess.run(['sudo', 'cp', tmp_path, file_path], 
                                  capture_output=True, text=True, timeout=3)
            os.unlink(tmp_path)
            
            if result.returncode == 0:
                print("Patch xloader appliqué avec succès")
            else:
                print(f" Erreur sudo: {result.stderr[:100]}")
        else:
            print(" Remplacement non effectué")
    else:
        print(" Pattern non trouvé dans le fichier")
        
except Exception as e:
    print(f" Erreur: {str(e)[:100]}")
PYTHON_PATCH

# Patcher aussi plugin.py pour ajouter la vérification de xloader_skip
XLOADER_PLUGIN_FILE="/srv/app/src/ckanext-xloader/ckanext/xloader/plugin.py"

if [ -f "$XLOADER_PLUGIN_FILE" ]; then
    if grep -q "xloader_skip.*Patched by databfc\|Patched by databfc.*xloader_skip" "$XLOADER_PLUGIN_FILE" 2>/dev/null; then
        echo "xloader plugin.py patch déjà appliqué"
    else
        echo "Application du patch xloader plugin.py pour xloader_skip..."
        
        timeout 5 python3 << 'PYTHON_PATCH2' 2>&1 || echo " Patch plugin.py non appliqué, continuons..."
import sys
import re

file_path = "/srv/app/src/ckanext-xloader/ckanext/xloader/plugin.py"

try:
    with open(file_path, 'r') as f:
        content = f.read()
    
    # Vérifier si le patch est déjà appliqué
    if 'xloader_skip' in content and 'Patched by databfc' in content.split('xloader_skip')[0]:
        print("Patch plugin.py déjà appliqué")
        sys.exit(0)
    
    # Ajouter la vérification de xloader_skip dans _submit_to_xloader
    # Chercher le pattern après la vérification url_type
    pattern = r'(if resource_dict\["url_type"\] in \("datapusher", "xloader"\):.*?return\s+)'
    
    replacement = r'''\1
        # Patched by databfc: skip resources marked with xloader_skip=True
        if resource_dict.get("xloader_skip", False):
            log.debug(
                "Skipping xloading resource {id} because "
                "xloader_skip=True".format(**resource_dict)
            )
            return
        '''
    
    new_content = re.sub(pattern, replacement, content, flags=re.DOTALL)
    
    if new_content != content:
        import subprocess
        import tempfile
        import os
        
        with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.py') as tmp_file:
            tmp_file.write(new_content)
            tmp_path = tmp_file.name
        
        result = subprocess.run(['sudo', 'cp', tmp_path, file_path], 
                              capture_output=True, text=True, timeout=3)
        os.unlink(tmp_path)
        
        if result.returncode == 0:
            print("Patch plugin.py appliqué avec succès")
        else:
            print(f" Erreur sudo plugin.py: {result.stderr[:100]}")
    else:
        print(" Pattern non trouvé dans plugin.py")
        
except Exception as e:
    print(f" Erreur plugin.py: {str(e)[:100]}")
PYTHON_PATCH2
    fi
fi

# NOTE: Le patch pour ajouter le header d'authentification dans _download_resource_data
# est maintenant géré par le script séparé 18-patch-xloader-auth-header.sh
# pour éviter les conflits et garder le code propre

echo "Configuration xloader permissions terminée"

