#!/bin/bash
# Script pour patcher xloader afin de nettoyer les noms de colonnes invalides

set -e

echo "Patching xloader pour nettoyer les noms de colonnes invalides..."

XLOADER_LOADER_FILE="/srv/app/src/ckanext-xloader/ckanext/xloader/loader.py"

if [ ! -f "$XLOADER_LOADER_FILE" ]; then
    echo " Fichier xloader/loader.py non trouvé, skip du patch"
    echo "Configuration xloader column names terminée (skip)"
    exit 0
fi

# Vérifier si le patch a déjà été appliqué
if grep -q "# Patched by databfc: clean invalid column names" "$XLOADER_LOADER_FILE" 2>/dev/null; then
    echo "Patch xloader column names déjà appliqué"
    exit 0
fi

echo "Application du patch xloader loader.py pour nettoyer les noms de colonnes..."

timeout 10 python3 << 'PYTHON_SCRIPT' 2>&1 || echo " Patch non appliqué (timeout ou erreur), continuons..."
import re
import sys
import subprocess
import tempfile
import os

file_path = "/srv/app/src/ckanext-xloader/ckanext/xloader/loader.py"

try:
    with open(file_path, 'r', encoding='utf-8') as f:
        content = f.read()
    
    # Fonction pour nettoyer les noms de colonnes invalides
    cleanup_function = '''
def _clean_column_name(name):
    """
    Nettoie un nom de colonne pour qu'il soit valide pour PostgreSQL/CKAN Datastore.
    
    Règles PostgreSQL:
    - Ne peut pas commencer par un underscore suivi d'une majuscule (_A, _B, etc.)
    - Doit être un identifiant valide (lettres, chiffres, underscores)
    
    Args:
        name: Nom de colonne à nettoyer
        
    Returns:
        Nom de colonne nettoyé
    """
    if not name:
        return name
    
    # Remplacer les caractères invalides par des underscores
    import re
    cleaned = re.sub(r'[^a-zA-Z0-9_]', '_', str(name))
    
    # Si commence par underscore suivi d'une majuscule, remplacer par minuscule
    if cleaned.startswith('_') and len(cleaned) > 1 and cleaned[1].isupper():
        cleaned = '_' + cleaned[1].lower() + cleaned[2:]
    
    # Si commence par un chiffre, préfixer avec 'col_'
    if cleaned and cleaned[0].isdigit():
        cleaned = 'col_' + cleaned
    
    # Si vide après nettoyage, utiliser un nom par défaut
    if not cleaned or cleaned == '_':
        cleaned = 'column_' + str(abs(hash(name)) % 10000)
    
    # Limiter la longueur (PostgreSQL limite à 63 caractères)
    if len(cleaned) > 63:
        cleaned = cleaned[:63]
    
    return cleaned
'''
    
    # Insérer la fonction après les imports (chercher après la dernière ligne d'import)
    lines = content.split('\n')
    insert_pos = 0
    for i, line in enumerate(lines):
        if i > 20 and (line.strip().startswith('class ') or line.strip().startswith('def ')):
            insert_pos = i
            break
    
    if insert_pos == 0:
        # Fallback: insérer après la ligne 30
        insert_pos = 30
    
    lines.insert(insert_pos, cleanup_function)
    content = '\n'.join(lines)
    
    # Chercher où nettoyer les noms de colonnes dans load_csv
    # Chercher l'appel à datastore_create et ajouter le nettoyage avant
    if 'datastore_create' in content and 'def load_csv' in content:
        # Chercher le pattern: p.toolkit.get_action('datastore_create')(context, data_dict)
        # Il faut trouver le bon contexte avec l'indentation
        # Chercher dans la fonction load_csv où datastore_create est appelé
        lines = content.split('\n')
        new_lines = []
        i = 0
        in_load_csv = False
        indent_level = 0
        
        while i < len(lines):
            line = lines[i]
            
            # Détecter le début de la fonction load_csv
            if 'def load_csv' in line:
                in_load_csv = True
                new_lines.append(line)
                i += 1
                continue
            
            # Si on est dans load_csv et qu'on trouve datastore_create
            if in_load_csv and 'datastore_create' in line and 'p.toolkit.get_action' in line:
                # Calculer l'indentation de cette ligne
                indent = len(line) - len(line.lstrip())
                
                # Vérifier si la ligne précédente est un try: (pour éviter les erreurs d'indentation)
                # Si oui, le code doit être indenté de 4 espaces supplémentaires par rapport au try:
                if len(new_lines) > 0:
                    prev_line = new_lines[-1].rstrip()
                    if prev_line.endswith('try:'):
                        # Si la ligne précédente est un try:, calculer son indentation
                        try_indent = len(new_lines[-1]) - len(new_lines[-1].lstrip())
                        # Le code dans le bloc try doit être indenté de 4 espaces supplémentaires
                        indent = try_indent + 4
                
                indent_str = ' ' * indent
                
                # Ajouter le nettoyage avant l'appel à datastore_create avec la bonne indentation
                new_lines.append(f"{indent_str}# Patched by databfc: clean invalid column names")
                new_lines.append(f"{indent_str}if 'fields' in data_dict and isinstance(data_dict['fields'], list):")
                new_lines.append(f"{indent_str}    for field in data_dict['fields']:")
                new_lines.append(f"{indent_str}        if isinstance(field, dict) and 'id' in field:")
                new_lines.append(f"{indent_str}            original_id = field['id']")
                new_lines.append(f"{indent_str}            cleaned_id = _clean_column_name(str(original_id))")
                new_lines.append(f"{indent_str}            if cleaned_id != original_id:")
                new_lines.append(f"{indent_str}                field['id'] = cleaned_id")
                new_lines.append(line)
                i += 1
                continue
            
            # Détecter la fin de la fonction load_csv (fonction suivante ou classe)
            if in_load_csv and (line.strip().startswith('def ') or line.strip().startswith('class ')) and 'def load_csv' not in line:
                in_load_csv = False
            
            new_lines.append(line)
            i += 1
        
        content = '\n'.join(new_lines)
    
    # Écrire le fichier modifié avec sudo
    with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.py', encoding='utf-8') as tmp_file:
        tmp_file.write(content)
        tmp_path = tmp_file.name
    
    result = subprocess.run(['sudo', 'cp', tmp_path, file_path], 
                          capture_output=True, text=True, timeout=5)
    os.unlink(tmp_path)
    
    if result.returncode == 0:
        print("Patch xloader column names appliqué avec succès")
    else:
        print(f" Erreur sudo: {result.stderr[:200]}")
        sys.exit(1)
    
except Exception as e:
    print(f" Erreur lors de l'application du patch: {e}")
    import traceback
    print(traceback.format_exc())
    sys.exit(1)

PYTHON_SCRIPT

if [ $? -eq 0 ]; then
    echo "Configuration xloader column names terminée"
else
    echo " Erreur lors du patch xloader column names (non bloquant)"
fi
