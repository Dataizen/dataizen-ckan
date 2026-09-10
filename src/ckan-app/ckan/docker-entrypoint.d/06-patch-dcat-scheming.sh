#!/bin/bash

# Patch ckanext-dcat pour gérer l'absence de scheming_datasets
# Ce patch évite l'erreur "Unknown dataset schema: dataset" quand scheming n'est pas configuré

echo "Application du patch pour ckanext-dcat (gestion absence scheming)..."

# Trouver le fichier base.py de ckanext-dcat
DCAT_BASE_PY="/usr/local/lib/python3.10/site-packages/ckanext/dcat/profiles/base.py"

# Si le fichier n'existe pas à cet emplacement, chercher ailleurs
if [ ! -f "$DCAT_BASE_PY" ]; then
    # Chercher dans les installations en développement (-e)
    DCAT_BASE_PY=$(find /srv/app -name "base.py" -path "*/ckanext-dcat/*/profiles/base.py" 2>/dev/null | head -1)
fi

if [ -z "$DCAT_BASE_PY" ] || [ ! -f "$DCAT_BASE_PY" ]; then
    echo " Fichier base.py de ckanext-dcat non trouvé, patch ignoré"
    exit 0
fi

echo "Fichier trouvé: $DCAT_BASE_PY"

# Vérifier si le patch a déjà été appliqué
if grep -q "# PATCH: Gestion absence scheming" "$DCAT_BASE_PY" 2>/dev/null; then
    echo "Patch déjà appliqué"
    exit 0
fi

# Vérifier les permissions avant de continuer
if [ ! -w "$DCAT_BASE_PY" ] && [ ! -w "$(dirname "$DCAT_BASE_PY")" ]; then
    echo " Permissions insuffisantes pour modifier le fichier, le patch Python sera utilisé à la place"
    echo "Le plugin dcat_patch gérera le patch au runtime"
    exit 0
fi

# Créer une sauvegarde
cp "$DCAT_BASE_PY" "${DCAT_BASE_PY}.bak" 2>/dev/null || {
    echo " Impossible de créer une sauvegarde, continuation quand même"
}

# Appliquer le patch avec Python (passer le chemin via variable d'environnement)
DCAT_BASE_PY="$DCAT_BASE_PY" python3 << 'PYTHON_PATCH'
import sys
import os
import re

# Récupérer le chemin du fichier depuis la variable d'environnement
file_path = os.getenv('DCAT_BASE_PY')

if not file_path:
    print(" Chemin du fichier non trouvé")
    sys.exit(0)

# Vérifier que le fichier existe
if not os.path.exists(file_path):
    print(f" Fichier non trouvé: {file_path}")
    sys.exit(0)

# Vérifier les permissions d'écriture
if not os.access(file_path, os.W_OK):
    print(" Pas de permissions d'écriture sur le fichier, le patch Python sera utilisé à la place")
    sys.exit(0)

try:
    with open(file_path, 'r', encoding='utf-8') as f:
        lines = f.readlines()
    
    modified = False
    new_lines = []
    i = 0
    
    while i < len(lines):
        line = lines[i]
        
        # Chercher le pattern: raise ObjectNotFound(f"Unknown dataset schema: {dataset_type}")
        # ou raise ObjectNotFound("Unknown dataset schema: ...")
        if 'raise ObjectNotFound' in line and 'Unknown dataset schema' in line:
            # Vérifier que c'est dans un bloc except
            # Regarder les lignes précédentes pour trouver le bloc try/except
            j = i - 1
            found_except = False
            while j >= 0 and j >= i - 10:  # Chercher jusqu'à 10 lignes en arrière
                if 'except' in lines[j] and (':' in lines[j] or 'ObjectNotFound' in lines[j] or 'Exception' in lines[j]):
                    found_except = True
                    break
                j -= 1
            
            if found_except:
                # Remplacer la ligne raise par schema = None
                # Conserver l'indentation
                indent = len(line) - len(line.lstrip())
                new_lines.append(' ' * indent + '# PATCH: Gestion absence scheming - retourner None pour utiliser le schéma par défaut\n')
                new_lines.append(' ' * indent + 'schema = None\n')
                modified = True
            else:
                new_lines.append(line)
        # Chercher aussi les patterns avec raise ObjectNotFound(...) sans f-string
        elif 'raise ObjectNotFound' in line and ('dataset schema' in line.lower() or 'schema' in line.lower()):
            # Vérifier le contexte
            j = i - 1
            found_except = False
            while j >= 0 and j >= i - 5:
                if 'except' in lines[j] and ':' in lines[j]:
                    found_except = True
                    break
                j -= 1
            
            if found_except:
                indent = len(line) - len(line.lstrip())
                new_lines.append(' ' * indent + '# PATCH: Gestion absence scheming\n')
                new_lines.append(' ' * indent + 'schema = None\n')
                modified = True
            else:
                new_lines.append(line)
        else:
            new_lines.append(line)
        
        i += 1
    
    if modified:
        with open(file_path, 'w', encoding='utf-8') as f:
            f.writelines(new_lines)
        print("Patch appliqué avec succès")
    else:
        print(" Pattern non trouvé, patch non appliqué")
        print("Le fichier peut avoir une structure différente, vérification manuelle recommandée")
        sys.exit(0)  # Ne pas échouer si le pattern n'est pas trouvé
            
except Exception as e:
    print(f"Erreur lors de l'application du patch: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)
PYTHON_PATCH

if [ $? -eq 0 ]; then
    echo "Patch ckanext-dcat terminé"
else
    echo " Échec du patch bash (permissions ou structure différente)"
    echo "Le patch Python sera utilisé à la place (via plugin dcat_patch)"
    # Ne pas restaurer la sauvegarde si elle n'existe pas ou si on n'a pas les permissions
    if [ -f "${DCAT_BASE_PY}.bak" ] && [ -w "$DCAT_BASE_PY" ]; then
        mv "${DCAT_BASE_PY}.bak" "$DCAT_BASE_PY"
    fi
    # Ne pas échouer complètement, le plugin Python fonctionnera
    exit 0
fi

