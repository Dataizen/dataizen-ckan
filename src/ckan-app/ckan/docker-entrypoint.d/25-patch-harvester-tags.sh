#!/bin/bash
set -uo pipefail

echo "Patch du harvester base pour nettoyer les tags..."

# Fichier à patcher
HARVESTER_BASE="/srv/app/src/ckanext-harvest/ckanext/harvest/harvesters/base.py"

if [ ! -f "$HARVESTER_BASE" ]; then
    echo "Fichier harvester base non trouvé: $HARVESTER_BASE"
    exit 0
fi

# Vérifier si le patch a déjà été appliqué
if grep -q "# Patch: nettoyage des tags avant package_create" "$HARVESTER_BASE"; then
    echo "Patch déjà appliqué"
    return 0 2>/dev/null || exit 0
fi

# Créer un backup (avec sudo si nécessaire)
if ! cp "$HARVESTER_BASE" "${HARVESTER_BASE}.backup" 2>/dev/null; then
    sudo cp "$HARVESTER_BASE" "${HARVESTER_BASE}.backup" 2>/dev/null || true
fi

# Ajouter le nettoyage des tags avant l'appel à package_create
python3 << 'PYTHON_PATCH'
import re
import sys

harvester_file = "/srv/app/src/ckanext-harvest/ckanext/harvest/harvesters/base.py"

with open(harvester_file, 'r') as f:
    content = f.read()

# Vérifier si le patch a déjà été appliqué
if "# Patch: nettoyage des tags avant package_create" in content:
    print("Patch déjà appliqué")
    sys.exit(0)

# Trouver la ligne avec package_create et ajouter le nettoyage avant
# Chercher le pattern: model.Session.flush() suivi de new_package = p.toolkit.get_action
pattern = r'(model\.Session\.flush\(\)\s+new_package = p\.toolkit\.get_action\()'

# Utiliser une fonction de remplacement pour éviter les problèmes d'échappement
def replace_func(match):
    return '''model.Session.flush()

                # Patch: nettoyage des tags avant package_create
                # Nettoyer les tags pour éviter les erreurs de validation
                if 'tags' in package_dict and package_dict['tags']:
                    import re
                    cleaned_tags = []
                    for tag in package_dict['tags']:
                        if isinstance(tag, dict):
                            tag_name = tag.get('name', '')
                        elif isinstance(tag, str):
                            tag_name = tag
                        else:
                            continue
                        
                        # Nettoyer le nom du tag
                        cleaned_name = tag_name.replace("'", "").replace("'", "").replace("`", "")
                        cleaned_name = re.sub(r'[^a-zA-Z0-9\\s\\-_.]', '', cleaned_name)
                        cleaned_name = re.sub(r'\\s+', ' ', cleaned_name).strip()
                        
                        if cleaned_name:
                            if isinstance(tag, dict):
                                cleaned_tags.append({'name': cleaned_name})
                            else:
                                cleaned_tags.append(cleaned_name)
                    package_dict['tags'] = cleaned_tags

                new_package = p.toolkit.get_action('''

new_content = re.sub(pattern, replace_func, content)

if new_content != content:
    try:
        with open(harvester_file, 'w') as f:
            f.write(new_content)
        print("Patch appliqué avec succès")
    except PermissionError:
        # Utiliser sudo si nécessaire
        import tempfile
        import subprocess
        with tempfile.NamedTemporaryFile(mode='w', delete=False) as tmp:
            tmp.write(new_content)
            tmp_path = tmp.name
        subprocess.run(['sudo', 'mv', tmp_path, harvester_file], check=True)
        subprocess.run(['sudo', 'chown', 'ckan:ckan-sys', harvester_file], check=False)
        print("Patch appliqué avec succès (via sudo)")
else:
    print("Pattern non trouvé, patch non appliqué")
PYTHON_PATCH

echo "Patch du harvester base terminé"

